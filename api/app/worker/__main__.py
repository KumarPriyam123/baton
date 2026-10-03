"""Worker process: `python -m app.worker`. The outbox runner plus the scheduled jobs (SPEC 9).

Several workers can run at once (`docker compose up --scale worker=2`): the claim hands each job to
one of them and the sweeps take an advisory lock. SIGTERM finishes the job in hand and exits."""

import asyncio
import contextlib
import signal

import structlog

from app.config import get_settings
from app.db.engine import create_engine
from app.logging import configure_logging
from app.worker.runner import Runner
from app.worker.schedules import run_schedules

log = structlog.get_logger("baton.worker")


async def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine = create_engine(settings)
    runner = Runner(engine)
    stop = asyncio.Event()

    def shut_down() -> None:
        stop.set()
        runner.stop()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):  # not available on Windows
            loop.add_signal_handler(sig, shut_down)

    log.info("worker_started", env=settings.env)
    try:
        await asyncio.gather(runner.run_forever(), run_schedules(engine, stop))
    finally:
        await engine.dispose()
        log.info("worker_stopped")


if __name__ == "__main__":
    asyncio.run(run())
