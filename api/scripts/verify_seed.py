"""Replay seeded histories and report problems: `python -m scripts.verify_seed [--sample N]`.

With no --sample every item is checked. Exit code 1 if anything is wrong.
"""

import argparse
import asyncio
import sys

import asyncpg

from app.config import get_settings
from app.db.urls import asyncpg_dsn
from scripts.seedlib.verify import verify_histories


async def run(sample: int | None) -> int:
    conn = await asyncpg.connect(asyncpg_dsn(get_settings().database_url))
    try:
        checked, problems = await verify_histories(conn, sample=sample)
    finally:
        await conn.close()
    for problem in problems[:50]:
        print(problem)
    print(f"checked {checked} items, {len(problems)} problems")
    return 1 if problems else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Check seeded work-item histories.")
    parser.add_argument("--sample", type=int, default=None, help="random items to check")
    sys.exit(asyncio.run(run(parser.parse_args().sample)))


if __name__ == "__main__":
    main()
