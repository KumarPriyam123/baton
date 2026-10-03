"""The command transaction (SPEC 6.4): one short transaction per command.

`command_tx` is a single attempt: it starts a transaction with tight lock and statement timeouts
and turns database errors into domain errors. `run_command` wraps it in the retry loop (a context
manager cannot run its own body twice), restarting the whole command on a serialization failure or
a deadlock.

No network calls inside `work`. Nothing is committed unless `work` returns.
"""

import asyncio
import random
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import errors
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
            yield CommandTx(conn, now or datetime.now(UTC), actor_id, request_id)
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
