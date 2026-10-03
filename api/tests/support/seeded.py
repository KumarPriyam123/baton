"""A scratch database holding the demo seed, committed, for tests that go through the app."""

import asyncio
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
from argon2 import PasswordHasher

from app.db.urls import asyncpg_dsn
from app.demo import DEMO_PASSWORD
from scripts.seedlib import generate, load

from .db import scratch_database


@dataclass(frozen=True)
class SeededDatabase:
    url: str
    dataset: generate.Dataset
    seconds: float  # generate + load

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(asyncpg_dsn(self.url))


async def _load(url: str, dataset: generate.Dataset) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        assert await load.load(conn, dataset, reset=False)
    finally:
        await conn.close()


def build_demo_dataset() -> generate.Dataset:
    return generate.build_dataset(
        "demo",
        seed=42,
        now=datetime.now(UTC),
        password_hash=PasswordHasher().hash(DEMO_PASSWORD),
    )


@contextmanager
def seeded_database(test_url: str) -> Iterator[SeededDatabase]:
    with scratch_database(test_url) as url:
        started = time.perf_counter()
        dataset = build_demo_dataset()
        asyncio.run(_load(url, dataset))
        yield SeededDatabase(url, dataset, time.perf_counter() - started)
