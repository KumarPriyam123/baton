"""The outbox runner (SPEC 9): claim a batch under a 60 s lease, dispatch each job to its handler by
topic, then mark it done, or schedule a retry with exponential backoff and jitter, or mark it dead
after 8 tries.

At-least-once delivery: a runner that dies keeps its jobs locked until the lease runs out, then
another one takes them, so every handler is idempotent. A handler that hangs is cut off when its
lease would expire (it counts as a failed try), so one poison job cannot block everything behind it.
The runner does not hold a lock while it works: `SKIP LOCKED` in the claim hands each job to one
runner, and the lease covers the time after the claim's transaction ends.
"""

import asyncio
import contextlib
import random
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

import structlog
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.tx import command_tx
from app.repo import outbox
from app.worker.handlers import HANDLERS, Handler

log = structlog.get_logger("baton.worker")

LEASE_SECONDS = 60.0
BATCH = 50
MAX_ATTEMPTS = 8
POLL_SECONDS = 1.0
MAX_ERROR = 500


def utcnow() -> datetime:
    return datetime.now(UTC)


class Runner:
    """One runner = one claim loop. Run several (processes, or objects in a test) side by side."""

    def __init__(
        self,
        engine: AsyncEngine,
        handlers: Mapping[str, Handler] | None = None,
        *,
        lease_seconds: float = LEASE_SECONDS,
        batch: int = BATCH,
        max_attempts: int = MAX_ATTEMPTS,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.engine = engine
        self.handlers = dict(HANDLERS if handlers is None else handlers)
        self.lease_seconds = lease_seconds
        self.batch = batch
        self.max_attempts = max_attempts
        self.clock = clock
        self.stopping = asyncio.Event()

    def stop(self) -> None:
        """Graceful shutdown (SIGTERM): finish the job in hand, release the rest, claim no more."""
        self.stopping.set()

    async def run_once(self) -> int:
        """Claim one batch and process it. Returns how many jobs were claimed."""
        async with self.engine.begin() as conn:
            jobs = await outbox.claim(conn, self.clock(), self.lease_seconds, self.batch)
        for position, job in enumerate(jobs):
            if self.stopping.is_set():
                async with self.engine.begin() as conn:
                    await outbox.release(conn, [j.id for j in jobs[position:]])
                break
            await self._process(job)
        return len(jobs)

    async def _process(self, job: outbox.Job) -> None:
        with structlog.contextvars.bound_contextvars(
            request_id=job.request_id, job_id=job.id, topic=job.topic, attempt=job.attempts
        ):
            try:
                handler = self.handlers.get(job.topic)
                if handler is None:
                    raise LookupError(f"no handler for topic {job.topic!r}")
                async with asyncio.timeout(self.lease_seconds):
                    await self._run_handler(handler, job)
            except asyncio.CancelledError:
                raise  # the process is going away: leave the lease, another runner takes over
            except Exception as error:
                await self._fail(job, error)
                return
            async with self.engine.begin() as conn:
                await outbox.complete(conn, job.id, self.clock())
            log.info("job_done")

    async def _run_handler(self, handler: Handler, job: outbox.Job) -> None:
        async with (
            self.engine.connect() as conn,
            command_tx(conn, request_id=job.request_id) as tx,
        ):
            await handler(tx, job.payload)

    async def _fail(self, job: outbox.Job, error: BaseException) -> None:
        text = f"{type(error).__name__}: {error}"[:MAX_ERROR]
        base = outbox.backoff_seconds(job.attempts)
        delay = base + random.uniform(0, base / 4)  # noqa: S311  (jitter, not security)
        async with self.engine.begin() as conn:
            status = await outbox.fail(conn, job, text, self.clock(), delay, self.max_attempts)
        log.warning("job_failed", error=text, status=status.value, retry_in=round(delay, 1))

    async def run_forever(self) -> None:
        while not self.stopping.is_set():
            try:
                claimed = await self.run_once()
            except Exception:
                log.exception("runner_error")
                claimed = 0
            if claimed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), timeout=POLL_SECONDS)
