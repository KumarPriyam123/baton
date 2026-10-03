"""Loads a Dataset into Postgres with COPY, in one transaction.

This is the one place besides record_event() that writes work_items and item_events (CLAUDE.md
I1): 600,000 events cannot go through the command path. It is a bulk loader for fake data, run
before the app starts, and verify.py checks every history it produces.
"""

import time
from collections.abc import Iterable

import asyncpg
import structlog

from . import generate as g

log = structlog.get_logger("baton.seed")

SEED_LOCK = 7_283_501  # advisory lock so two seeds never run at once

TABLES = (
    "users, teams, memberships, work_items, item_events, comments, approvals, watchers, "
    "item_reads, notifications, item_similar, idempotency_keys, outbox, sessions"
)

# Notifications for recent events, fanned out to each item's watchers (never to the actor).
# Deterministic: about 60 % are already read, chosen by event id, not by random().
NOTIFICATIONS_SQL = """
INSERT INTO notifications (user_id, item_id, event_id, kind, created_at, read_at)
SELECT w.user_id, e.item_id, e.id, e.kind, e.created_at,
       CASE WHEN e.id % 10 < 6 THEN LEAST(e.created_at + interval '2 hours', now()) END
  FROM item_events e
  JOIN watchers w ON w.item_id = e.item_id
 WHERE e.created_at > now() - make_interval(days => $1)
   AND e.actor_id IS DISTINCT FROM w.user_id
   AND e.kind IN ('assigned', 'unassigned', 'blocked', 'resolved', 'reopened', 'closed',
                  'transferred', 'approval_requested', 'approval_approved',
                  'approval_rejected', 'commented', 'sla_breached')
"""


async def users_exist(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT EXISTS (SELECT 1 FROM users)"))


async def _copy(
    conn: asyncpg.Connection, table: str, columns: Iterable[str], rows: list[tuple[object, ...]]
) -> None:
    started = time.perf_counter()
    await conn.copy_records_to_table(table, records=rows, columns=list(columns))
    log.info(
        "seed_copied", table=table, rows=len(rows), seconds=round(time.perf_counter() - started, 2)
    )


async def load(conn: asyncpg.Connection, ds: g.Dataset, *, reset: bool) -> bool:
    """Returns False (and writes nothing) when data exists and reset was not asked for."""
    spec = g.SIZES[ds.size]
    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", SEED_LOCK)
        if await users_exist(conn):
            if not reset:
                return False
            # TRUNCATE is not covered by the item_events trigger (SPEC 3.2 lists UPDATE and
            # DELETE); a reset needs it, and it only ever runs from this script.
            await conn.execute(f"TRUNCATE {TABLES} RESTART IDENTITY CASCADE")

        # asyncpg's binary COPY has no encoder for citext; users are few, so INSERT with a cast.
        await conn.executemany(
            "INSERT INTO users (id, email, name, password_hash, is_admin, created_at) "
            "VALUES ($1, $2::text::citext, $3, $4, $5, $6)",
            ds.users,
        )
        await _copy(conn, "teams", g.TEAM_COLS, ds.teams)
        await _copy(conn, "memberships", g.MEMBERSHIP_COLS, ds.memberships)
        await _copy(conn, "work_items", g.ITEM_COLS, ds.items)
        # Events go in global time order, so identity ids follow time (cursors, BRIN index).
        await _copy(conn, "item_events", g.EVENT_COLS, ds.events)
        await _copy(conn, "comments", g.COMMENT_COLS, ds.comments)
        await _copy(conn, "approvals", g.APPROVAL_COLS, ds.approvals)
        await _copy(conn, "watchers", g.WATCHER_COLS, ds.watchers)
        await _copy(conn, "item_reads", g.READ_COLS, ds.reads)
        await _copy(conn, "item_similar", g.SIMILAR_COLS, ds.similar)

        await conn.execute(
            "UPDATE work_items w SET last_event_id = e.last_id "
            "FROM (SELECT item_id, max(id) AS last_id FROM item_events GROUP BY item_id) e "
            "WHERE e.item_id = w.id"
        )
        await conn.execute(
            "UPDATE teams t SET item_seq = "
            "(SELECT count(*) FROM work_items i WHERE i.origin_team_id = t.id)"
        )
        status = await conn.execute(NOTIFICATIONS_SQL, spec.notification_days)
        log.info("seed_notifications", result=status)
    await conn.execute("ANALYZE")
    return True
