"""Seed the database: `python -m scripts.seed --size demo|large [--reset]`.

Idempotent: does nothing when users already exist, unless --reset (which wipes everything).
After loading, a sample of histories is replayed and checked; any problem fails the run.
"""

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime

import asyncpg
import structlog
from argon2 import PasswordHasher

from app.config import get_settings
from app.db.urls import asyncpg_dsn
from app.logging import configure_logging
from scripts.seedlib import generate, load, verify
from scripts.seedlib.personas import DEMO_PASSWORD

log = structlog.get_logger("baton.seed")


async def run(
    size: str, *, reset: bool, seed: int, verify_sample: int, dry_run: bool = False
) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    started = time.perf_counter()

    if dry_run:  # generate only: handy for tuning sizes without touching the database
        dataset = generate.build_dataset(
            size,
            seed=seed,
            now=datetime.now(UTC),
            password_hash="dry-run",  # noqa: S106
        )
        log.info(
            "seed_dry_run", seconds=round(time.perf_counter() - started, 2), **dataset.counts()
        )
        return 0

    conn = await asyncpg.connect(asyncpg_dsn(settings.database_url))
    try:
        if await load.users_exist(conn) and not reset:
            log.info("seed_skipped", reason="users already exist; pass --reset to wipe and reseed")
            return 0

        step = time.perf_counter()
        password_hash = PasswordHasher().hash(DEMO_PASSWORD)  # one hash, shared by every user
        dataset = generate.build_dataset(
            size, seed=seed, now=datetime.now(UTC), password_hash=password_hash
        )
        log.info("seed_generated", seconds=round(time.perf_counter() - step, 2), **dataset.counts())

        if not await load.load(conn, dataset, reset=reset):
            log.info("seed_skipped", reason="another seed filled the database first")
            return 0

        checked, problems = await verify.verify_histories(conn, sample=verify_sample, seed=seed)
        for problem in problems[:20]:
            log.error("seed_history_invalid", problem=problem)
        log.info(
            "seed_done",
            size=size,
            seconds=round(time.perf_counter() - started, 2),
            histories_checked=checked,
            problems=len(problems),
        )
        return 1 if problems else 0
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Baton with demo or large data.")
    parser.add_argument("--size", choices=sorted(generate.SIZES), default="demo")
    parser.add_argument("--reset", action="store_true", help="wipe existing data first")
    parser.add_argument("--seed", type=int, default=42, help="random seed (same seed, same data)")
    parser.add_argument("--verify", type=int, default=20, help="histories to replay afterwards")
    parser.add_argument("--dry-run", action="store_true", help="generate and count, do not load")
    args = parser.parse_args()
    sys.exit(
        asyncio.run(
            run(
                args.size,
                reset=args.reset,
                seed=args.seed,
                verify_sample=args.verify,
                dry_run=args.dry_run,
            )
        )
    )


if __name__ == "__main__":
    main()
