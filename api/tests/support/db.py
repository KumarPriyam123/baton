"""Scratch databases for tests that need committed data or a particular migration state."""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path

import asyncpg
from alembic import command
from alembic.config import Config

from app.db.urls import asyncpg_dsn, with_database

API_DIR = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.attributes["url"] = url
    return config


async def _admin_execute(test_url: str, sql: str) -> None:
    admin = await asyncpg.connect(asyncpg_dsn(with_database(test_url, "postgres")))
    try:
        await admin.execute(sql)
    finally:
        await admin.close()


@contextmanager
def scratch_database(test_url: str, *, migrate_to: str | None = "head") -> Iterator[str]:
    """An empty database on the test server (migrated to `migrate_to`), dropped afterwards."""
    name = f"baton_scratch_{uuid.uuid4().hex[:10]}"
    asyncio.run(_admin_execute(test_url, f'CREATE DATABASE "{name}"'))
    url = with_database(test_url, name)
    try:
        if migrate_to is not None:
            command.upgrade(alembic_config(url), migrate_to)
        yield url
    finally:
        asyncio.run(_admin_execute(test_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))


@asynccontextmanager
async def ascratch_database(
    test_url: str, *, migrate_to: str | None = "head"
) -> AsyncIterator[str]:
    """Same as scratch_database, for use inside an async test (asyncio.run cannot nest)."""
    name = f"baton_scratch_{uuid.uuid4().hex[:10]}"
    await _admin_execute(test_url, f'CREATE DATABASE "{name}"')
    url = with_database(test_url, name)
    try:
        if migrate_to is not None:
            await asyncio.to_thread(command.upgrade, alembic_config(url), migrate_to)
        yield url
    finally:
        await _admin_execute(test_url, f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
