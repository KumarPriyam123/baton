"""The command transaction (SPEC 6.4): one short transaction per command.

`command_tx` is a single attempt: it starts a transaction with tight lock and statement timeouts
and turns database errors into domain errors. `run_command` wraps it in the retry loop (a context
manager cannot run its own body twice), restarting the whole command on a serialization failure or
a deadlock.

No network calls inside `work`. Nothing is committed unless `work` returns.
"""

import asyncio
import json
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import errors, schema
from app.domain.enums import EventKind
from app.domain.errors import Busy

LOCK_TIMEOUT = "2s"
STATEMENT_TIMEOUT = "5s"
MAX_ATTEMPTS = 3


@dataclass
class CommandTx:
    """What a command needs to know about itself: who acts, when, and under which request."""

    conn: AsyncConnection
    now: datetime
    actor_id: uuid.UUID | None = None
    request_id: str | None = None
    event_ids: list[int] = field(default_factory=list)  # filled by record_event
    pinned: bool = False  # a caller (a test, the worker's clock) fixed `now`

    def restamp(self) -> None:
        """Take the time again. `now` is first read when the transaction starts, but a command can
        then wait (key insert, row lock) for up to the lock timeout. Called right after the lock
        is won, so `updated_at` and event times follow commit order instead of arrival order."""
        if not self.pinned:
            self.now = datetime.now(UTC)


@asynccontextmanager
async def command_tx(
    conn: AsyncConnection,
    *,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> AsyncIterator[CommandTx]:
    # Authentication already read on this connection and left a transaction open. SET LOCAL
    # belongs to a transaction that is the command's own, so close that one first.
    if conn.in_transaction():
        await conn.commit()
    try:
        async with conn.begin():
            await conn.execute(sa.text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
            await conn.execute(sa.text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
            yield CommandTx(
                conn, now or datetime.now(UTC), actor_id, request_id, pinned=now is not None
            )
    except DBAPIError as error:
        # Also reached for errors raised by COMMIT itself, after the body has returned.
        mapped = errors.translate(error)
        if mapped is None:
            raise
        raise mapped from error


async def run_command[T](
    conn: AsyncConnection,
    work: Callable[[CommandTx], Awaitable[T]],
    *,
    actor_id: uuid.UUID | None = None,
    request_id: str | None = None,
    now: datetime | None = None,
) -> T:
    """Run `work` in a command transaction; restart it (up to 3 attempts) when Postgres rolls it
    back with 40001 or 40P01. `work` must therefore be safe to run again from the start: it may
    only touch the database, and everything it did is rolled back before the next attempt."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            async with command_tx(conn, actor_id=actor_id, request_id=request_id, now=now) as tx:
                return await work(tx)
        except DBAPIError as error:
            if errors.sqlstate(error) not in errors.RETRYABLE:
                raise
            if attempt == MAX_ATTEMPTS:
                raise Busy(
                    "The system is busy. Try again in a moment.",
                    headers={"Retry-After": errors.RETRY_AFTER_SECONDS},
                ) from error
            await asyncio.sleep(random.uniform(0, 0.02 * attempt))  # noqa: S311  (jitter)
    raise AssertionError("unreachable")  # pragma: no cover


# ----- history (CLAUDE.md I1) -----------------------------------------------------------------

OUTBOX_TOPIC = "item.event"
CREATED_TOPIC = "item.created"
NOTIFY_CHANNEL = "item_changes"
NOTIFICATIONS_CHANNEL = "notifications"


async def enqueue(
    conn: AsyncConnection,
    topic: str,
    payload: dict[str, Any],
    *,
    dedupe_key: str | None = None,
) -> None:
    """Add a job to the outbox in the caller's transaction (I7). `dedupe_key` is UNIQUE: a second
    job with the same key is dropped, so a retried command cannot queue the work twice."""
    await conn.execute(
        pg_insert(schema.outbox)
        .values(topic=topic, payload=payload, dedupe_key=dedupe_key)
        .on_conflict_do_nothing(index_elements=["dedupe_key"])
    )


async def notify_user(conn: AsyncConnection, user_id: uuid.UUID) -> None:
    """Tell the live-update layer (phase 7) that `user_id` has a new notification. Delivered only
    if the transaction commits."""
    await conn.execute(
        sa.text("SELECT pg_notify(:channel, :payload)"),
        {"channel": NOTIFICATIONS_CHANNEL, "payload": json.dumps({"user_id": str(user_id)})},
    )


class EventSubject(Protocol):
    """The item right after the change this event describes (a row of work_items)."""

    @property
    def id(self) -> uuid.UUID: ...
    @property
    def team_id(self) -> uuid.UUID: ...
    @property
    def version(self) -> int: ...
    @property
    def requester_id(self) -> uuid.UUID: ...
    @property
    def assignee_id(self) -> uuid.UUID | None: ...
    @property
    def confidential(self) -> bool: ...


async def record_event(
    tx: CommandTx,
    item: EventSubject,
    kind: EventKind,
    *,
    data: dict[str, Any] | None = None,
    reason: str | None = None,
    is_decision: bool = False,
    prev_team_id: uuid.UUID | None = None,
) -> int:
    """Write one event of a command, in the command's transaction. Every change to a work item
    goes through here (I1). It writes, together:

    - the `item_events` row, carrying the version the item has AFTER the change (the current
      version for comment, sla_breached and duplicate_suggested, which never bump it);
    - `work_items.last_event_id` and `updated_at`, plus `last_activity_at` when a person wrote
      the event (system events have no actor and must not reset "going stale");
    - an outbox row (topic item.event) for the worker;
    - NOTIFY item_changes, delivered only if the transaction commits.
    """
    conn = tx.conn
    event_id = int(
        (
            await conn.execute(
                sa.insert(schema.item_events)
                .values(
                    item_id=item.id,
                    team_id=item.team_id,
                    actor_id=tx.actor_id,
                    kind=kind.value,
                    item_version=item.version,
                    data=data or {},
                    reason=reason,
                    is_decision=is_decision,
                    request_id=tx.request_id,
                    created_at=tx.now,
                )
                .returning(schema.item_events.c.id)
            )
        ).scalar_one()
    )
    stamp: dict[str, Any] = {"last_event_id": event_id, "updated_at": tx.now}
    if tx.actor_id is not None:
        stamp["last_activity_at"] = tx.now
    stamped = (
        await conn.execute(
            sa.update(schema.work_items)
            .where(schema.work_items.c.id == item.id)
            .values(**stamp)
            .returning(
                schema.work_items.c.version,
                schema.work_items.c.team_id,
                schema.work_items.c.key,
            )
        )
    ).one()
    if (stamped.version, stamped.team_id) != (item.version, item.team_id):
        # The caller described a different item state than the row has: its event would carry a
        # wrong version (I2). A bug, never a client error; the whole command rolls back.
        raise RuntimeError(
            f"record_event: item {item.id} is at version {stamped.version}, not {item.version}"
        )
    await conn.execute(
        sa.insert(schema.outbox).values(
            topic=OUTBOX_TOPIC, payload={"event_ids": [event_id], "request_id": tx.request_id}
        )
    )
    notice = {
        "event_id": event_id,
        "item_id": str(item.id),
        "item_key": stamped.key,
        "version": item.version,
        "team_id": str(item.team_id),
        "prev_team_id": str(prev_team_id) if prev_team_id else None,
        "requester_id": str(item.requester_id),
        "assignee_id": str(item.assignee_id) if item.assignee_id else None,
        "confidential": item.confidential,
    }
    await conn.execute(
        sa.text("SELECT pg_notify(:channel, :payload)"),
        {"channel": NOTIFY_CHANNEL, "payload": json.dumps(notice, separators=(",", ":"))},
    )
    tx.event_ids.append(event_id)
    return event_id
