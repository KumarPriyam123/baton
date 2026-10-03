"""Work-item queries and the only writes to `work_items` (CLAUDE.md I1).

Reads: one statement per request returns everything the item DTO shows (joins, one LATERAL for
the latest approval), so a list of 100 rows costs the same number of statements as a list of 1.
Every query that returns or counts items filters with `visibility_clause()` in the WHERE clause,
before LIMIT (I3).

Writes: `allocate_number`, `insert_item` and `update_item` are called from item commands inside a
command transaction, followed by `record_event()` (db/tx.py), which writes the history and stamps
`last_event_id`. Nothing else writes `work_items`; tests/unit/test_single_writer.py enforces it.
"""

import base64
import binascii
import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql import Select, Subquery
from sqlalchemy.sql.elements import ColumnElement

from app.db import schema
from app.domain.enums import (
    ApprovalStatus,
    DueSource,
    ItemStatus,
    ItemType,
    Resolution,
)
from app.domain.errors import ValidationFailed
from app.domain.policy import ActorContext, visibility_clause

wi = schema.work_items.c

# ----- the item as one flat record ------------------------------------------------------------


@dataclass(frozen=True)
class ItemRecord:
    id: uuid.UUID
    key: str
    version: int
    last_event_id: int | None
    team_id: uuid.UUID
    team_key: str
    team_name: str
    type: ItemType
    title: str
    description: str
    priority: int
    status: ItemStatus
    resolution: Resolution | None
    resolution_note: str | None
    duplicate_of: str | None  # key of the item this one duplicates
    confidential: bool
    requires_approval: bool
    requester_id: uuid.UUID
    requester_name: str
    assignee_id: uuid.UUID | None
    assignee_name: str | None
    due_at: datetime | None
    due_source: DueSource
    sla_breached_at: datetime | None
    last_activity_at: datetime
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None
    approval_id: uuid.UUID | None
    approval_status: ApprovalStatus | None
    approval_requested_by_id: uuid.UUID | None
    approval_requested_by_name: str | None
    approval_requested_at: datetime | None
    approval_decided_by_id: uuid.UUID | None
    approval_decided_by_name: str | None
    approval_decided_at: datetime | None
    last_event_kind: str | None
    last_event_actor_id: uuid.UUID | None
    last_event_actor_name: str | None
    last_event_at: datetime | None
    unread_since_event_id: int | None
    watching: bool
    blocked_reason: str | None

    @staticmethod
    def from_row(row: sa.Row[Any]) -> "ItemRecord":
        values = dict(row._mapping)
        values["type"] = ItemType(values["type"])
        values["status"] = ItemStatus(values["status"])
        if values["resolution"] is not None:
            values["resolution"] = Resolution(values["resolution"])
        values["due_source"] = DueSource(values["due_source"])
        if values["approval_status"] is not None:
            values["approval_status"] = ApprovalStatus(values["approval_status"])
        return ItemRecord(**values)


# Every column of work_items except the generated tsvector, which no item view needs.
_COLUMNS = [c for c in schema.work_items.columns if c.name != "search"]


def _page(*where: ColumnElement[bool]) -> Select[Any]:
    """The rows of work_items that match, before any join: filter, order and LIMIT go here so
    that the joins below only ever run for the rows of one page, not for the whole table."""
    return sa.select(*_COLUMNS).where(*where)


def _item_query(ctx: ActorContext, page: Subquery) -> Select[Any]:
    """The item view over `page`, a subquery of `_page()` that already has its LIMIT."""
    b = page.c
    t = schema.teams.c
    requester = schema.users.alias("requester")
    assignee = schema.users.alias("assignee")
    duplicate = schema.work_items.alias("duplicate")
    ev = schema.item_events.alias("last_event")
    ev_actor = schema.users.alias("last_event_actor")
    reads = schema.item_reads
    ap_requester = schema.users.alias("approval_requester")
    ap_decider = schema.users.alias("approval_decider")
    ap_t = schema.approvals.c
    # The newest approval of the item, if any (approvals_item_idx).
    latest_approval = (
        sa.select(
            ap_t.id,
            ap_t.status,
            ap_t.requested_by,
            ap_t.requested_at,
            ap_t.decided_by,
            ap_t.decided_at,
        )
        .where(ap_t.item_id == b.id)
        .order_by(ap_t.requested_at.desc())
        .limit(1)
        .lateral("approval")
    )
    blocked_reason = (
        sa.select(schema.item_events.c.reason)
        .where(
            schema.item_events.c.item_id == b.id,
            schema.item_events.c.kind == "blocked",
        )
        .order_by(schema.item_events.c.id.desc())
        .limit(1)
        .scalar_subquery()
    )
    source = (
        page.join(schema.teams, t.id == b.team_id)
        .join(requester, requester.c.id == b.requester_id)
        .outerjoin(assignee, assignee.c.id == b.assignee_id)
        .outerjoin(
            duplicate,
            # The key of the original is shown only to someone who may see the original (I3).
            sa.and_(duplicate.c.id == b.duplicate_of_id, visibility_clause(ctx, duplicate.c)),
        )
        .outerjoin(ev, ev.c.id == b.last_event_id)
        .outerjoin(ev_actor, ev_actor.c.id == ev.c.actor_id)
        .outerjoin(reads, sa.and_(reads.c.item_id == b.id, reads.c.user_id == ctx.user_id))
        .outerjoin(latest_approval, sa.true())
        .outerjoin(ap_requester, ap_requester.c.id == latest_approval.c.requested_by)
        .outerjoin(ap_decider, ap_decider.c.id == latest_approval.c.decided_by)
    )
    return sa.select(
        b.id,
        b.key,
        b.version,
        b.last_event_id,
        b.team_id,
        t.key.label("team_key"),
        t.name.label("team_name"),
        b.type,
        b.title,
        b.description,
        b.priority,
        b.status,
        b.resolution,
        b.resolution_note,
        duplicate.c.key.label("duplicate_of"),
        b.confidential,
        b.requires_approval,
        b.requester_id,
        requester.c.name.label("requester_name"),
        b.assignee_id,
        assignee.c.name.label("assignee_name"),
        b.due_at,
        b.due_source,
        b.sla_breached_at,
        b.last_activity_at,
        b.created_at,
        b.updated_at,
        b.resolved_at,
        b.closed_at,
        latest_approval.c.id.label("approval_id"),
        latest_approval.c.status.label("approval_status"),
        latest_approval.c.requested_by.label("approval_requested_by_id"),
        ap_requester.c.name.label("approval_requested_by_name"),
        latest_approval.c.requested_at.label("approval_requested_at"),
        latest_approval.c.decided_by.label("approval_decided_by_id"),
        ap_decider.c.name.label("approval_decided_by_name"),
        latest_approval.c.decided_at.label("approval_decided_at"),
        ev.c.kind.label("last_event_kind"),
        ev.c.actor_id.label("last_event_actor_id"),
        ev_actor.c.name.label("last_event_actor_name"),
        ev.c.created_at.label("last_event_at"),
        sa.case(
            (
                sa.and_(
                    reads.c.last_read_event_id.is_not(None),
                    b.last_event_id > reads.c.last_read_event_id,
                ),
                reads.c.last_read_event_id,
            ),
            else_=None,
        ).label("unread_since_event_id"),
        sa.exists()
        .where(schema.watchers.c.item_id == b.id, schema.watchers.c.user_id == ctx.user_id)
        .label("watching"),
        sa.case((b.status == ItemStatus.BLOCKED.value, blocked_reason), else_=None).label(
            "blocked_reason"
        ),
    ).select_from(source)


async def get_visible_by_key(
    conn: AsyncConnection, ctx: ActorContext, key: str
) -> ItemRecord | None:
    """The item the actor may see, or None (missing and hidden look the same, SPEC 5.1)."""
    page = _page(wi.key == key, visibility_clause(ctx)).subquery("page")
    query = _item_query(ctx, page)
    row = (await conn.execute(query)).first()
    return ItemRecord.from_row(row) if row else None


async def get_by_key_unchecked(
    conn: AsyncConnection, ctx: ActorContext, key: str
) -> ItemRecord | None:
    """The item with no visibility filter, for the answer to a command whose actor may have just
    given up the right to see it (a lead who transfers an item away, decision 38). Only a command
    that has already authorised the actor may call this; never a read endpoint."""
    return _first(await conn.execute(_item_query(ctx, _page(wi.key == key).subquery("page"))))


def _first(result: sa.CursorResult[Any]) -> ItemRecord | None:
    row = result.first()
    return ItemRecord.from_row(row) if row else None


async def visible_item_id(conn: AsyncConnection, ctx: ActorContext, key: str) -> uuid.UUID | None:
    """The id of an item the actor may see (for its events), or None."""
    found = (
        await conn.execute(sa.select(wi.id).where(wi.key == key, visibility_clause(ctx)))
    ).scalar_one_or_none()
    return uuid.UUID(str(found)) if found is not None else None


# ----- what a command needs while it holds the row lock ------------------------------------


@dataclass(frozen=True)
class LockedItem:
    id: uuid.UUID
    key: str
    version: int
    team_id: uuid.UUID
    team_key: str
    team_name: str
    type: ItemType
    title: str
    description: str
    priority: int
    status: ItemStatus
    resolution: Resolution | None
    duplicate_of_id: uuid.UUID | None
    due_at: datetime | None
    due_source: DueSource
    created_at: datetime
    confidential: bool
    requires_approval: bool
    requester_id: uuid.UUID
    assignee_id: uuid.UUID | None
    pending_approval_requested_by: uuid.UUID | None


async def lock_by_key(conn: AsyncConnection, key: str) -> LockedItem | None:
    """SELECT ... FOR UPDATE on the item row, first thing in every command (SPEC 6.4). Hidden
    or not, the row is locked; the caller decides what the actor may know about it.

    The lock statement touches `work_items` alone. If it also joined `teams` (or read the pending
    approval in a subquery), a command that waited for the lock would re-check those parts against
    the snapshot from before the wait: after a concurrent transfer the join would match nothing and
    the actor would be told the item does not exist. The team and the approval are read by a second
    statement, which in READ COMMITTED sees everything committed by then."""
    t, ap = schema.teams.c, schema.approvals.c
    row = (
        await conn.execute(
            sa.select(
                wi.id,
                wi.key,
                wi.version,
                wi.team_id,
                wi.type,
                wi.title,
                wi.description,
                wi.priority,
                wi.status,
                wi.resolution,
                wi.duplicate_of_id,
                wi.due_at,
                wi.due_source,
                wi.created_at,
                wi.confidential,
                wi.requires_approval,
                wi.requester_id,
                wi.assignee_id,
            )
            .where(wi.key == key)
            .with_for_update()
        )
    ).first()
    if row is None:
        return None
    team = (await conn.execute(sa.select(t.key, t.name).where(t.id == row.team_id))).one()
    pending_by = (
        await conn.execute(
            sa.select(ap.requested_by).where(
                ap.item_id == row.id, ap.status == ApprovalStatus.PENDING.value
            )
        )
    ).scalar_one_or_none()
    values = dict(row._mapping)
    values["team_key"], values["team_name"] = team.key, team.name
    values["pending_approval_requested_by"] = pending_by
    values["type"] = ItemType(values["type"])
    values["status"] = ItemStatus(values["status"])
    if values["resolution"] is not None:
        values["resolution"] = Resolution(values["resolution"])
    values["due_source"] = DueSource(values["due_source"])
    return LockedItem(**values)


# ----- writes -------------------------------------------------------------------------------

_RETURNED = (
    wi.id,
    wi.key,
    wi.team_id,
    wi.version,
    wi.requester_id,
    wi.assignee_id,
    wi.confidential,
)


@dataclass(frozen=True)
class ChangedItem:
    """The item right after a write: what record_event needs to describe it."""

    id: uuid.UUID
    key: str
    team_id: uuid.UUID
    version: int
    requester_id: uuid.UUID
    assignee_id: uuid.UUID | None
    confidential: bool


def _changed(row: sa.Row[Any]) -> ChangedItem:
    return ChangedItem(**dict(row._mapping))


async def allocate_number(conn: AsyncConnection, team_id: uuid.UUID) -> tuple[str, int]:
    """The next item number of a team. The UPDATE locks the team row until the create commits,
    which is the whole price of gap-free numbers (SPEC 6.5); a rollback gives the number back."""
    t = schema.teams
    row = (
        await conn.execute(
            sa.update(t)
            .where(t.c.id == team_id)
            .values(item_seq=t.c.item_seq + 1)
            .returning(t.c.key, t.c.item_seq)
        )
    ).one()
    return f"{row.key}-{row.item_seq}", int(row.item_seq)


async def insert_item(conn: AsyncConnection, values: Mapping[str, Any]) -> ChangedItem:
    row = (
        await conn.execute(sa.insert(schema.work_items).values(**values).returning(*_RETURNED))
    ).one()
    return _changed(row)


def _stored(values: Mapping[str, Any]) -> dict[str, Any]:
    """Enum members become the strings Postgres stores."""
    return {k: v.value if isinstance(v, Enum) else v for k, v in values.items()}


async def update_item(
    conn: AsyncConnection,
    item_id: uuid.UUID,
    expected_version: int,
    values: Mapping[str, Any],
    now: datetime,
) -> ChangedItem | None:
    """Change decision-relevant fields and bump the version by exactly 1 (SPEC 6.2). The
    `version = :expected` guard repeats what the row lock already guarantees: a belt for the
    braces. None means the version had moved."""
    row = (
        await conn.execute(
            sa.update(schema.work_items)
            .where(wi.id == item_id, wi.version == expected_version)
            .values(**_stored(values), version=wi.version + 1, updated_at=now)
            .returning(*_RETURNED)
        )
    ).first()
    return _changed(row) if row else None


async def claim_item(
    conn: AsyncConnection, item_id: uuid.UUID, values: Mapping[str, Any], now: datetime
) -> ChangedItem | None:
    """SPEC 6.1: the claim is ONE conditional UPDATE and its WHERE clause is the judge. Under
    READ COMMITTED a second claimant waits for the first to commit, re-checks the WHERE against
    the committed row, and matches nothing. None means it lost; the caller reads why."""
    row = (
        await conn.execute(
            sa.update(schema.work_items)
            .where(
                wi.id == item_id,
                wi.assignee_id.is_(None),
                wi.status == ItemStatus.NEW.value,
            )
            .values(**_stored(values), version=wi.version + 1, updated_at=now, last_activity_at=now)
            .returning(*_RETURNED)
        )
    ).first()
    return _changed(row) if row else None


async def lock_keys_owned_by(
    conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID, limit: int
) -> list[str]:
    """Keys of the open items this person works in this team, locked in id order (so two
    commands that lock several items never wait on each other in a circle). Served by
    work_items_assignee_open_idx. Deliberately not filtered by visibility (see
    teams.count_open_items_owned): only a team lead or an admin reaches it."""
    finished = [ItemStatus.RESOLVED.value, ItemStatus.CLOSED.value]
    rows = await conn.execute(
        sa.select(wi.key)
        .where(wi.team_id == team_id, wi.assignee_id == user_id, wi.status.notin_(finished))
        .order_by(wi.id)
        .limit(limit)
        .with_for_update()
    )
    return [str(k) for k in rows.scalars()]


async def add_watcher(conn: AsyncConnection, item_id: uuid.UUID, user_id: uuid.UUID) -> None:
    await conn.execute(
        pg_insert(schema.watchers)
        .values(item_id=item_id, user_id=user_id)
        .on_conflict_do_nothing(index_elements=["item_id", "user_id"])
    )


# ----- filters, sorts, keyset pagination (SPEC 8) -----------------------------------------------


@dataclass(frozen=True)
class ItemFilters:
    team: str | None = None  # team key
    status: Sequence[ItemStatus] = ()
    priority: Sequence[int] = ()
    type: Sequence[ItemType] = ()
    assignee: str | uuid.UUID | None = None  # "me" | "none" | user id
    requester: str | uuid.UUID | None = None  # "me" | user id
    overdue: bool | None = None
    confidential: bool | None = None
    updated_since: datetime | None = None


_FINISHED = [ItemStatus.RESOLVED.value, ItemStatus.CLOSED.value]


def filter_clauses(
    ctx: ActorContext, filters: ItemFilters, now: datetime
) -> list[ColumnElement[bool]]:
    clauses: list[ColumnElement[bool]] = [visibility_clause(ctx)]
    if filters.team is not None:
        clauses.append(
            wi.team_id
            == sa.select(schema.teams.c.id)
            .where(schema.teams.c.key == filters.team)
            .scalar_subquery()
        )
    if filters.status:
        clauses.append(wi.status.in_([s.value for s in filters.status]))
    if filters.priority:
        clauses.append(wi.priority.in_(list(filters.priority)))
    if filters.type:
        clauses.append(wi.type.in_([t.value for t in filters.type]))
    if filters.assignee == "none":
        clauses.append(wi.assignee_id.is_(None))
    elif filters.assignee is not None:
        clauses.append(
            wi.assignee_id == (ctx.user_id if filters.assignee == "me" else filters.assignee)
        )
    if filters.requester is not None:
        clauses.append(
            wi.requester_id == (ctx.user_id if filters.requester == "me" else filters.requester)
        )
    if filters.overdue is not None:
        late = sa.and_(wi.due_at < now, wi.status.notin_(_FINISHED))
        clauses.append(late if filters.overdue else sa.not_(sa.func.coalesce(late, sa.false())))
    if filters.confidential is not None:
        clauses.append(wi.confidential.is_(filters.confidential))
    if filters.updated_since is not None:
        clauses.append(wi.updated_at >= filters.updated_since)
    return clauses


_INFINITY = sa.literal_column("'infinity'::timestamptz", type_=sa.DateTime(timezone=True))


def _due(columns: Any) -> ColumnElement[Any]:
    """Null due dates sort last, in ORDER BY and in the cursor alike."""
    return sa.func.coalesce(columns.due_at, _INFINITY)


@dataclass(frozen=True)
class Sort:
    keys: Callable[[Any], tuple[ColumnElement[Any], ...]]  # columns of work_items or of a page
    descending: bool


SORTS: dict[str, Sort] = {
    "priority": Sort(lambda c: (c.priority, _due(c), c.id), descending=False),
    "due": Sort(lambda c: (_due(c), c.id), descending=False),
    "updated": Sort(lambda c: (c.updated_at, c.id), descending=True),
    "created": Sort(lambda c: (c.created_at, c.id), descending=True),
}


def _cursor_values(sort: str, item: ItemRecord) -> list[Any]:
    due = item.due_at.isoformat() if item.due_at else None
    match sort:
        case "priority":
            return [item.priority, due, str(item.id)]
        case "due":
            return [due, str(item.id)]
        case "updated":
            return [item.updated_at.isoformat(), str(item.id)]
        case _:
            return [item.created_at.isoformat(), str(item.id)]


def encode_item_cursor(sort: str, last: ItemRecord) -> str:
    raw = json.dumps({"s": sort, "v": _cursor_values(sort, last)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _invalid_cursor() -> ValidationFailed:
    return ValidationFailed(
        "That cursor is not valid for this sort.",
        errors=[{"field": "cursor", "message": "Invalid cursor.", "type": "value_error"}],
    )


def _after(sort: str, cursor: str) -> list[Any]:
    """The bind values of the last row of the previous page, typed like the sort keys."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        if payload["s"] != sort:
            raise _invalid_cursor()
        raw = payload["v"]
        timestamp = sa.DateTime(timezone=True)
        match sort:
            case "priority":
                priority, due, row_id = raw
                return [
                    sa.literal(int(priority), sa.SmallInteger()),
                    sa.func.coalesce(
                        sa.literal(datetime.fromisoformat(due) if due else None, timestamp),
                        _INFINITY,
                    ),
                    sa.literal(uuid.UUID(row_id), schema.work_items.c.id.type),
                ]
            case "due":
                due, row_id = raw
                return [
                    sa.func.coalesce(
                        sa.literal(datetime.fromisoformat(due) if due else None, timestamp),
                        _INFINITY,
                    ),
                    sa.literal(uuid.UUID(row_id), schema.work_items.c.id.type),
                ]
            case _:
                moment, row_id = raw
                return [
                    sa.literal(datetime.fromisoformat(moment), timestamp),
                    sa.literal(uuid.UUID(row_id), schema.work_items.c.id.type),
                ]
    except (binascii.Error, ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise _invalid_cursor() from error


async def list_items(
    conn: AsyncConnection,
    ctx: ActorContext,
    filters: ItemFilters,
    *,
    sort: str,
    cursor: str | None,
    limit: int,
    now: datetime,
) -> tuple[list[ItemRecord], str | None]:
    """One keyset page. Returns the rows and the cursor of the next page (None at the end).

    No OFFSET (I8): the page after row R is `WHERE (sort keys) > (R's sort keys)`, with the id as
    the last key, so ties can never skip or repeat a row.
    """
    spec = SORTS[sort]
    inner = _page(*filter_clauses(ctx, filters, now))
    if cursor is not None:
        after = sa.tuple_(*_after(sort, cursor))
        keys = sa.tuple_(*spec.keys(wi))
        inner = inner.where(keys < after if spec.descending else keys > after)

    def ordered(columns: Any) -> list[Any]:
        return [k.desc() if spec.descending else k.asc() for k in spec.keys(columns)]

    window = inner.order_by(*ordered(wi)).limit(limit + 1).subquery("page")
    query = _item_query(ctx, window).order_by(*ordered(window.c))
    rows = (await conn.execute(query)).all()
    records = [ItemRecord.from_row(r) for r in rows]
    page = records[:limit]
    more = len(records) > limit
    return page, encode_item_cursor(sort, page[-1]) if more and page else None


async def facet_counts(
    conn: AsyncConnection, ctx: ActorContext, filters: ItemFilters, now: datetime
) -> dict[str, dict[str, int]]:
    """Counts per status, priority, type and team for the same filters, in one pass."""
    t = schema.teams.c
    rows = await conn.execute(
        sa.select(wi.status, wi.priority, wi.type, t.key, sa.func.count().label("n"))
        .select_from(schema.work_items.join(schema.teams, t.id == wi.team_id))
        .where(*filter_clauses(ctx, filters, now))
        .group_by(sa.func.grouping_sets(wi.status, wi.priority, wi.type, t.key))
    )
    out: dict[str, dict[str, int]] = {"status": {}, "priority": {}, "type": {}, "team": {}}
    for row in rows:
        # Each output row belongs to exactly one grouping set; the columns are never NULL, so the
        # one that is not NULL says which.
        if row.status is not None:
            out["status"][str(row.status)] = row.n
        elif row.priority is not None:
            out["priority"][str(row.priority)] = row.n
        elif row.type is not None:
            out["type"][str(row.type)] = row.n
        else:
            out["team"][str(row.key)] = row.n
    return out
