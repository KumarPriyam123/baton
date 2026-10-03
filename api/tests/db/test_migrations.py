"""The migration is reversible and repeatable, and there is exactly one history."""

import asyncio
import uuid
from collections.abc import AsyncIterator

import asyncpg
import pytest
from alembic import command
from alembic.script import ScriptDirectory

from app.db.urls import asyncpg_dsn, with_database

from .conftest import alembic_config

SNAPSHOT_QUERIES = {
    "tables": """
        SELECT table_name FROM information_schema.tables
         WHERE table_schema = 'public' ORDER BY 1""",
    "columns": """
        SELECT table_name, column_name, data_type, udt_name, is_nullable, column_default,
               is_generated, generation_expression
          FROM information_schema.columns
         WHERE table_schema = 'public' ORDER BY 1, ordinal_position""",
    "indexes": "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1",
    "constraints": """
        SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid)
          FROM pg_constraint WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2""",
    "triggers": """
        SELECT tgname, pg_get_triggerdef(oid) FROM pg_trigger
         WHERE NOT tgisinternal ORDER BY 1""",
    "enums": """
        SELECT t.typname, string_agg(e.enumlabel, ',' ORDER BY e.enumsortorder)
          FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid GROUP BY 1 ORDER BY 1""",
    "functions": """
        SELECT proname FROM pg_proc WHERE pronamespace = 'public'::regnamespace ORDER BY 1""",
    "extensions": "SELECT extname FROM pg_extension WHERE extname <> 'plpgsql' ORDER BY 1",
}


@pytest.fixture
async def scratch_url(test_database_url: str) -> AsyncIterator[str]:
    """An empty database on the same server, so the round trip cannot disturb other tests."""
    name = f"baton_roundtrip_{uuid.uuid4().hex[:8]}"
    admin = await asyncpg.connect(asyncpg_dsn(with_database(test_database_url, "postgres")))
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
        yield with_database(test_database_url, name)
    finally:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        await admin.close()


async def _snapshot(url: str) -> dict[str, list[tuple[object, ...]]]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return {
            key: [tuple(row.values()) for row in await conn.fetch(sql)]
            for key, sql in SNAPSHOT_QUERIES.items()
        }
    finally:
        await conn.close()


async def _alembic(action: str, url: str, revision: str) -> None:
    # Alembic's env.py runs its own event loop, so it needs a thread of its own.
    run = getattr(command, action)
    await asyncio.to_thread(run, alembic_config(url), revision)


async def test_upgrade_downgrade_upgrade_round_trip_is_clean(scratch_url: str) -> None:
    await _alembic("upgrade", scratch_url, "head")
    first = await _snapshot(scratch_url)
    assert len(first["tables"]) == 15  # 14 SPEC tables + alembic_version

    await _alembic("downgrade", scratch_url, "base")
    emptied = await _snapshot(scratch_url)
    assert emptied["tables"] == [("alembic_version",)]
    for key in ("columns", "constraints", "triggers", "enums", "functions", "extensions"):
        remaining = [row for row in emptied[key] if row[0] != "alembic_version"]
        assert not remaining, f"downgrade left {key}: {remaining}"
    assert [row[0] for row in emptied["indexes"]] == ["alembic_version_pkc"]

    await _alembic("upgrade", scratch_url, "head")
    assert await _snapshot(scratch_url) == first


async def test_upgrade_is_a_no_op_when_already_at_head(scratch_url: str) -> None:
    await _alembic("upgrade", scratch_url, "head")
    before = await _snapshot(scratch_url)

    await _alembic("upgrade", scratch_url, "head")

    assert await _snapshot(scratch_url) == before


def test_there_is_exactly_one_migration_head(test_database_url: str) -> None:
    script = ScriptDirectory.from_config(alembic_config(test_database_url))
    assert len(script.get_heads()) == 1
