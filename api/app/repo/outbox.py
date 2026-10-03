"""The outbox from the worker's side (SPEC 9): claim a batch under a lease, then complete, fail or
release each job. `enqueue` (the producer side) is in db/tx.py, in the transaction of the change.

Delivery is at least once. A claim sets `locked_until`; a job whose runner died is claimable again
once the lease has passed, and its `attempts` already counts the lost try. Completing and failing
are guarded by `status = 'pending'`, so a runner that lost its lease and finishes late cannot undo
a job another runner already completed.
"""

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import OutboxStatus

o = schema.outbox.c

BACKOFF_CAP_SECONDS = 300

# SPEC 9, with the clock passed in (never now() in SQL, so tests can move it).
_CLAIM = sa.text(
    """
    UPDATE outbox
       SET locked_until = CAST(:now AS timestamptz) + make_interval(secs => CAST(:lease AS float8)),
           attempts = attempts + 1
     WHERE id IN (SELECT id FROM outbox
                   WHERE status = 'pending' AND available_at <= CAST(:now AS timestamptz)
                     AND (locked_until IS NULL OR locked_until < CAST(:now AS timestamptz))
                   ORDER BY id FOR UPDATE SKIP LOCKED LIMIT :batch)
    RETURNING id, topic, payload, attempts
    """
)


@dataclass(frozen=True)
class Job:
    id: int
    topic: str
    payload: dict[str, Any]
    attempts: int  # including this try

    @property
    def request_id(self) -> str | None:
        value = self.payload.get("request_id")
        return str(value) if value else None


async def claim(
    conn: AsyncConnection, now: datetime, lease_seconds: float, batch: int
) -> list[Job]:
    rows = await conn.execute(_CLAIM, {"now": now, "lease": lease_seconds, "batch": batch})
    return [Job(r.id, r.topic, _payload(r.payload), r.attempts) for r in rows.all()]


def _payload(raw: Any) -> dict[str, Any]:
    value = json.loads(raw) if isinstance(raw, str) else raw
    return dict(value) if isinstance(value, dict) else {}


def backoff_seconds(attempts: int) -> int:
    """SPEC 9: min(2^attempts, 300) seconds (jitter is added by the caller)."""
    return int(min(2**attempts, BACKOFF_CAP_SECONDS))


async def complete(conn: AsyncConnection, job_id: int, now: datetime) -> None:
    await conn.execute(
        sa.update(schema.outbox)
        .where(o.id == job_id, o.status == OutboxStatus.PENDING.value)
        .values(status=OutboxStatus.DONE.value, processed_at=now, locked_until=None)
    )


async def fail(
    conn: AsyncConnection,
    job: Job,
    error: str,
    now: datetime,
    delay_seconds: float,
    max_attempts: int,
) -> OutboxStatus:
    """Retry later, or give up (`dead`) once `max_attempts` tries have been used."""
    dead = job.attempts >= max_attempts
    status = OutboxStatus.DEAD if dead else OutboxStatus.PENDING
    await conn.execute(
        sa.update(schema.outbox)
        .where(o.id == job.id, o.status == OutboxStatus.PENDING.value)
        .values(
            status=status.value,
            last_error=error[:2000],
            locked_until=None,
            available_at=now + timedelta(seconds=delay_seconds),
        )
    )
    return status


async def release(conn: AsyncConnection, job_ids: list[int]) -> None:
    """Give back jobs that were claimed but never started (graceful shutdown): they are available
    at once and the unused try is not counted."""
    if job_ids:
        await conn.execute(
            sa.update(schema.outbox)
            .where(o.id.in_(job_ids), o.status == OutboxStatus.PENDING.value)
            .values(locked_until=None, attempts=o.attempts - 1)
        )


# ----- admin (SPEC 11) --------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRecord:
    id: int
    topic: str
    status: OutboxStatus
    attempts: int
    available_at: datetime
    last_error: str | None
    created_at: datetime
    processed_at: datetime | None
    payload: dict[str, Any]


def _record(row: sa.Row[Any]) -> JobRecord:
    return JobRecord(
        id=row.id,
        topic=row.topic,
        status=OutboxStatus(row.status),
        attempts=row.attempts,
        available_at=row.available_at,
        last_error=row.last_error,
        created_at=row.created_at,
        processed_at=row.processed_at,
        payload=_payload(row.payload),
    )


async def list_jobs(
    conn: AsyncConnection, status: OutboxStatus | None, before_id: int | None, limit: int
) -> tuple[list[JobRecord], int | None]:
    """Newest first, a keyset page by id (I8). `status` uses the outbox indexes where it can."""
    query = sa.select(schema.outbox).order_by(o.id.desc()).limit(limit + 1)
    if status is not None:
        query = query.where(o.status == status.value)
    if before_id is not None:
        query = query.where(o.id < before_id)
    rows = (await conn.execute(query)).all()
    page = [_record(r) for r in rows[:limit]]
    return page, page[-1].id if len(rows) > limit else None


async def requeue_dead(conn: AsyncConnection, job_id: int, now: datetime) -> bool | None:
    """Dead -> pending, attempts 0, available now. None: no such job; False: it is not dead."""
    changed = (
        await conn.execute(
            sa.update(schema.outbox)
            .where(o.id == job_id, o.status == OutboxStatus.DEAD.value)
            .values(
                status=OutboxStatus.PENDING.value,
                attempts=0,
                available_at=now,
                locked_until=None,
            )
            .returning(o.id)
        )
    ).first()
    if changed is not None:
        return True
    exists = (await conn.execute(sa.select(o.id).where(o.id == job_id))).first()
    return False if exists else None
