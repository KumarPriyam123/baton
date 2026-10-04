"""Comments, watchers and read markers (SPEC 3.2). Writes here never touch `work_items`: a comment
reaches the item's history through `record_event` (db/tx.py), the other two are per-user state."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.policy import ActorContext, visibility_clause

wi = schema.work_items.c


async def insert_comment(
    conn: AsyncConnection, item_id: uuid.UUID, author_id: uuid.UUID, body: str, now: datetime
) -> int:
    return int(
        (
            await conn.execute(
                sa.insert(schema.comments)
                .values(item_id=item_id, author_id=author_id, body=body, created_at=now)
                .returning(schema.comments.c.id)
            )
        ).scalar_one()
    )


async def remove_watcher(conn: AsyncConnection, item_id: uuid.UUID, user_id: uuid.UUID) -> None:
    w = schema.watchers.c
    await conn.execute(sa.delete(schema.watchers).where(w.item_id == item_id, w.user_id == user_id))


async def mark_read(conn: AsyncConnection, ctx: ActorContext, key: str, now: datetime) -> bool:
    """Record that the actor has seen everything up to the item's newest event. One statement:
    the newest event id is read and the visibility rule applied in the same snapshot as the write,
    so a read marker can neither point past an event the actor was never shown nor exist for an
    item they cannot see. `GREATEST` keeps the marker from ever moving backwards. Returns False
    when the item is missing or hidden (the caller answers 404)."""
    r = schema.item_reads
    source = sa.select(sa.literal(ctx.user_id), wi.id, wi.last_event_id, sa.literal(now)).where(
        wi.key == key, wi.last_event_id.is_not(None), visibility_clause(ctx)
    )
    insert = pg_insert(r).from_select(
        ["user_id", "item_id", "last_read_event_id", "read_at"], source
    )
    stmt = insert.on_conflict_do_update(
        index_elements=[r.c.user_id, r.c.item_id],
        set_={
            "last_read_event_id": sa.func.greatest(
                r.c.last_read_event_id, insert.excluded.last_read_event_id
            ),
            "read_at": insert.excluded.read_at,
        },
    ).returning(r.c.item_id)
    row = (await conn.execute(stmt)).first()
    if row is None:
        return False
    # Seen is seen, whichever way the item was opened: its notifications for this person are read.
    n = schema.notifications.c
    await conn.execute(
        sa.update(schema.notifications)
        .where(n.user_id == ctx.user_id, n.item_id == row.item_id, n.read_at.is_(None))
        .values(read_at=now)
    )
    return True
