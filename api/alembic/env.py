"""Async Alembic environment (asyncpg). Migrations are hand-written SQL via op.execute."""

import asyncio

import structlog
from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.logging import configure_logging

config = context.config
log = structlog.get_logger("baton.migrate")


def _database_url() -> str:
    # Tests pass the URL programmatically; the CLI falls back to DATABASE_URL.
    url = config.attributes.get("url") or config.get_main_option("sqlalchemy.url")
    if url:
        return str(url)
    from app.config import get_settings

    return get_settings().database_url


def _run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    configure_logging("info")
    log.info("migrate_start")
    engine = create_async_engine(_database_url(), poolclass=pool.NullPool)
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()
    log.info("migrate_done")


if context.is_offline_mode():
    raise SystemExit("Offline (--sql) mode is not supported; run against a database.")

asyncio.run(_run_async())
