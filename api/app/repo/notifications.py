"""A person's notifications (SPEC 3.2, 11). The worker fills the table (phase 6); this module only
reads it and marks rows read.

A notification points at an item, so every read joins `work_items` and filters with
`visibility_clause()` in the WHERE clause, before the LIMIT: a notification about an item the
person can no longer see (a confidential item they lost access to) is neither listed nor
paged past (I3). Marking read only ever touches the caller's own rows.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.policy import ActorContext, visibility_clause

n = schema.notifications.c
wi = schema.work_items.c


@dataclass(frozen=True)
class NotificationRecord:
    id: int
    kind: str
    item_key: str
    item_title: str
    event_id: int
    actor_id: uuid.UUID | None
    actor_name: str | None
    created_at: datetime
    read_at: datetime | None


async def list_notifications(
    conn: AsyncConnection,
    ctx: ActorContext,
    *,
    unread_only: bool,
    before_id: int | None,
    limit: int,
) -> tuple[list[NotificationRecord], int | None]:
    """Newest first, a keyset page by notification id (no OFFSET, I8). `unread_only` is served by
    the partial index `notifications_unread_idx`."""
    ev, actor = schema.item_events.c, schema.users.c
    query = (
        sa.select(
            n.id,
            n.kind,
            wi.key.label("item_key"),
            wi.title.label("item_title"),
            n.event_id,
            ev.actor_id,
            actor.name.label("actor_name"),
            n.created_at,
            n.read_at,
        )
        .select_from(
            schema.notifications.join(schema.work_items, wi.id == n.item_id)
            .join(schema.item_events, ev.id == n.event_id)
            .outerjoin(schema.users, actor.id == ev.actor_id)
        )
        .where(n.user_id == ctx.user_id, visibility_clause(ctx))
        .order_by(n.id.desc())
        .limit(limit + 1)
    )
    if unread_only:
        query = query.where(n.read_at.is_(None))
    if before_id is not None:
        query = query.where(n.id < before_id)
    rows = (await conn.execute(query)).all()
    page = [NotificationRecord(**dict(r._mapping)) for r in rows[:limit]]
    return page, page[-1].id if len(rows) > limit else None


async def mark_read(
    conn: AsyncConnection, user_id: uuid.UUID, ids: list[int] | None, now: datetime
) -> int:
    """Mark the caller's unread notifications read: the listed ids, or every one when `ids` is
    None. Rows of other people are never matched, whatever ids are sent."""
    stmt: Any = (
        sa.update(schema.notifications)
        .where(n.user_id == user_id, n.read_at.is_(None))
        .values(read_at=now)
        .returning(n.id)
    )
    if ids is not None:
        stmt = stmt.where(n.id.in_(ids))
    return len((await conn.execute(stmt)).all())


# ----- fan-out (the worker's side) --------------------------------------------------------------


@dataclass(frozen=True)
class EventToNotify:
    id: int
    kind: str
    item_id: uuid.UUID
    actor_id: uuid.UUID | None
    team_id: uuid.UUID
    requester_id: uuid.UUID
    assignee_id: uuid.UUID | None
    confidential: bool
    status: str


async def event_to_notify(conn: AsyncConnection, event_id: int) -> EventToNotify | None:
    """The event and the item as it is now. Who may see the item is judged against the item now, not
    when the event happened: someone who lost access since must not be told about it."""
    ev = schema.item_events.c
    row = (
        await conn.execute(
            sa.select(
                ev.id,
                ev.kind,
                ev.item_id,
                ev.actor_id,
                wi.team_id,
                wi.requester_id,
                wi.assignee_id,
                wi.confidential,
                wi.status,
            )
            .select_from(schema.item_events.join(schema.work_items, wi.id == ev.item_id))
            .where(ev.id == event_id)
        )
    ).first()
    return EventToNotify(**dict(row._mapping)) if row else None


async def candidate_user_ids(conn: AsyncConnection, e: EventToNotify) -> set[uuid.UUID]:
    """Requester, assignee and watchers, minus the actor and anyone deactivated (SPEC 9)."""
    w, u = schema.watchers.c, schema.users.c
    wanted = {e.requester_id}
    if e.assignee_id is not None:
        wanted.add(e.assignee_id)
    wanted.update(
        (await conn.execute(sa.select(w.user_id).where(w.item_id == e.item_id))).scalars()
    )
    if e.actor_id is not None:
        wanted.discard(e.actor_id)
    active = await conn.execute(sa.select(u.id).where(u.id.in_(wanted), u.deactivated_at.is_(None)))
    return {uuid.UUID(str(user_id)) for user_id in active.scalars()}


async def insert_notifications(
    conn: AsyncConnection, e: EventToNotify, user_ids: list[uuid.UUID], now: datetime
) -> list[uuid.UUID]:
    """One row per user; `UNIQUE (user_id, event_id)` makes a second delivery of the same job add
    nothing. Returns the users who got a NEW notification."""
    if not user_ids:
        return []
    stmt = (
        pg_insert(schema.notifications)
        .values(
            [
                {
                    "user_id": user_id,
                    "item_id": e.item_id,
                    "event_id": e.id,
                    "kind": e.kind,
                    "created_at": now,
                }
                for user_id in user_ids
            ]
        )
        .on_conflict_do_nothing(index_elements=["user_id", "event_id"])
        .returning(n.user_id)
    )
    return [uuid.UUID(str(uid)) for uid in (await conn.execute(stmt)).scalars()]


async def unread_count(conn: AsyncConnection, ctx: ActorContext, *, cap: int) -> int:
    """How many notifications are unread and visible, counted up to `cap` (the bell shows "20+",
    so counting further is wasted work). Visibility applies to counts too (I3)."""
    inner = (
        sa.select(sa.literal(1))
        .select_from(schema.notifications.join(schema.work_items, wi.id == n.item_id))
        .where(n.user_id == ctx.user_id, n.read_at.is_(None), visibility_clause(ctx))
        .limit(cap)
        .subquery()
    )
    return int((await conn.execute(sa.select(sa.func.count()).select_from(inner))).scalar_one())
