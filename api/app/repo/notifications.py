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
