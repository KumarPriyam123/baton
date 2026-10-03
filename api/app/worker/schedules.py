"""Scheduled jobs (SPEC 9). Only the worker runs them, never an API process, and each takes a
Postgres advisory lock, so with several workers one sweep runs at a time and the others skip it.

The SLA sweep stamps `sla_breached_at` once per overdue item and writes one `sla_breached` event for
it through `record_event`; the notify handler then fans that event out like any other."""

import asyncio
import contextlib
from datetime import datetime

import sqlalchemy as sa
import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.tx import CommandTx, command_tx, record_event
from app.domain.enums import EventKind
from app.repo import items as items_repo

log = structlog.get_logger("baton.worker")

SLA_LOCK_ID = 7_310_001  # pg_try_advisory_xact_lock key of the SLA sweep
SLA_INTERVAL_SECONDS = 60.0
SLA_BATCH = 100  # items per transaction (I8)


async def _try_lock(tx: CommandTx, lock_id: int) -> bool:
    return bool(
        (
            await tx.conn.execute(sa.text("SELECT pg_try_advisory_xact_lock(:id)"), {"id": lock_id})
        ).scalar_one()
    )


async def sla_sweep(engine: AsyncEngine, *, now: datetime | None = None) -> int:
    """Mark every overdue open item, `SLA_BATCH` per transaction. Returns how many this call marked
    (0 when another sweep holds the lock). Running it twice at once marks each item once."""
    marked = 0
    while True:
        async with engine.connect() as conn, command_tx(conn, now=now) as tx:
            if not await _try_lock(tx, SLA_LOCK_ID):
                return marked
            batch = await items_repo.mark_sla_breached(tx.conn, tx.now, SLA_BATCH)
            for item, due_at in batch:
                await record_event(
                    tx, item, EventKind.SLA_BREACHED, data={"due_at": due_at.isoformat()}
                )
        marked += len(batch)
        if len(batch) < SLA_BATCH:
            return marked


async def run_schedules(engine: AsyncEngine, stop: asyncio.Event) -> None:
    """The scheduler loop of one worker: the SLA sweep every 60 s until `stop` is set."""
    while not stop.is_set():
        try:
            marked = await sla_sweep(engine)
            if marked:
                log.info("sla_sweep", marked=marked)
        except Exception:
            log.exception("sla_sweep_failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=SLA_INTERVAL_SECONDS)
