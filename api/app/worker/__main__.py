"""Worker process: `python -m app.worker`. Phase 0 is a heartbeat; the outbox runner is phase 6."""

import asyncio
import contextlib
import signal

import structlog

from app.config import get_settings
from app.logging import configure_logging

HEARTBEAT_SECONDS = 10

log = structlog.get_logger("baton.worker")


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, stop.set)

    log.info("worker_started", env=settings.env)
    while not stop.is_set():
        log.info("worker_heartbeat")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=HEARTBEAT_SECONDS)
    log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
