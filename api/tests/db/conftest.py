import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import asyncpg
import pytest
from alembic import command
from alembic.config import Config

from app.db.urls import asyncpg_dsn

from .factories import World, make_world

API_DIR = Path(__file__).resolve().parents[2]


def alembic_config(url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(API_DIR / "alembic"))
    config.attributes["url"] = url
    return config


async def _reset_schema(url: str) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        # Dropping the schema also drops the extensions, enums and alembic_version in it.
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_database(test_database_url: str) -> str:
    """The test database at Alembic head, rebuilt once per session from an empty schema."""
    asyncio.run(_reset_schema(test_database_url))
    command.upgrade(alembic_config(test_database_url), "head")
    return test_database_url


@pytest.fixture
async def conn(migrated_database: str) -> AsyncIterator[asyncpg.Connection]:
    """A connection inside a transaction that is always rolled back: tests leave no rows."""
    connection = await asyncpg.connect(asyncpg_dsn(migrated_database))
    transaction = connection.transaction()
    await transaction.start()
    try:
        yield connection
    finally:
        await transaction.rollback()
        await connection.close()


@pytest.fixture
async def world(conn: asyncpg.Connection) -> World:
    return await make_world(conn)
