"""Reads of the append-only `item_events` table (writes are `record_event` in db/tx.py)."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import EventKind
from app.domain.workflow import NON_VERSIONING_EVENT_KINDS

# changes_since is for one stale editor, not for history: a bound, not a page (I8).
MAX_CHANGES_SINCE = 100


@dataclass(frozen=True)
class EventRecord:
    id: int
    kind: EventKind
    actor_id: uuid.UUID | None
    actor_name: str | None
    item_version: int
    data: dict[str, Any]
    reason: str | None
    is_decision: bool
    created_at: datetime
    comment_body: str | None = None  # the text, for `commented` events only


def _query() -> sa.Select[Any]:
    e, u, c = schema.item_events.c, schema.users.c, schema.comments.c
    return sa.select(
        e.id,
        e.kind,
        e.actor_id,
        u.name.label("actor_name"),
        e.item_version,
        e.data,
        e.reason,
        e.is_decision,
        e.created_at,
        c.body.label("comment_body"),
    ).select_from(
        schema.item_events.outerjoin(schema.users, u.id == e.actor_id).outerjoin(
            schema.comments,
            # The comment text rides along with its event, so the timeline is one query (SPEC 3.2).
            sa.and_(
                e.kind == EventKind.COMMENTED.value,
                c.id == sa.cast(e.data["comment_id"].astext, sa.BigInteger),
            ),
        )
    )


def _record(row: sa.Row[Any]) -> EventRecord:
    return EventRecord(
        id=row.id,
        kind=EventKind(row.kind),
        actor_id=row.actor_id,
        actor_name=row.actor_name,
        item_version=row.item_version,
        data=row.data,
        reason=row.reason,
        is_decision=row.is_decision,
        created_at=row.created_at,
        comment_body=row.comment_body,
    )


async def list_events(
    conn: AsyncConnection,
    item_id: uuid.UUID,
    *,
    after_id: int,
    descending: bool,
    limit: int,
) -> tuple[list[EventRecord], int | None]:
    """One keyset page of an item's events by event id (index item_events_item_idx), oldest
    first or newest first. Returns the rows and the id to continue from (None at the end).

    `after_id` is the last id of the previous page in either direction: ascending continues with
    larger ids, descending with smaller ones. The caller has already checked that the actor may
    see the item.
    """
    e = schema.item_events.c
    query = _query().where(e.item_id == item_id)
    if descending:
        if after_id > 0:
            query = query.where(e.id < after_id)
        query = query.order_by(e.id.desc())
    else:
        query = query.where(e.id > after_id).order_by(e.id.asc())
    rows = (await conn.execute(query.limit(limit + 1))).all()
    page = [_record(r) for r in rows[:limit]]
    return page, page[-1].id if len(rows) > limit else None


async def last_assigned_at(conn: AsyncConnection, item_id: uuid.UUID) -> datetime | None:
    """When the item last got an owner (for "Asha took this 4 seconds ago")."""
    e = schema.item_events.c
    return (
        await conn.execute(
            sa.select(e.created_at)
            .where(e.item_id == item_id, e.kind == EventKind.ASSIGNED.value)
            .order_by(e.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def changes_since(
    conn: AsyncConnection, item_id: uuid.UUID, version: int
) -> list[EventRecord]:
    """The versioned events after `version`, oldest first: exactly what changed under a stale
    editor (SPEC 6.2). Comments and system notes never bump the version, so they are left out."""
    e = schema.item_events.c
    skipped = [k.value for k in NON_VERSIONING_EVENT_KINDS]
    rows = await conn.execute(
        _query()
        .where(e.item_id == item_id, e.item_version > version, e.kind.notin_(skipped))
        .order_by(e.id.asc())
        .limit(MAX_CHANGES_SINCE)
    )
    return [_record(r) for r in rows]
