"""app/db/schema.py and app/domain/enums.py must describe the database that Alembic builds."""

from enum import StrEnum

import asyncpg
import pytest
import sqlalchemy as sa

from app.db import schema
from app.domain import enums

ENUM_TYPES: dict[str, type[StrEnum]] = {
    "team_role": enums.TeamRole,
    "item_type": enums.ItemType,
    "item_status": enums.ItemStatus,
    "resolution": enums.Resolution,
    "approval_status": enums.ApprovalStatus,
    "due_source": enums.DueSource,
    "outbox_status": enums.OutboxStatus,
}


def _tables() -> list[sa.Table]:
    return sorted(schema.metadata.tables.values(), key=lambda t: t.name)


@pytest.mark.parametrize("name", sorted(ENUM_TYPES))
async def test_python_enum_has_exactly_the_database_labels(
    conn: asyncpg.Connection, name: str
) -> None:
    rows = await conn.fetch(
        "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
        "WHERE t.typname = $1 ORDER BY e.enumsortorder",
        name,
    )

    assert [row["enumlabel"] for row in rows] == [m.value for m in ENUM_TYPES[name]]


async def test_event_kinds_match_the_check_constraint(conn: asyncpg.Connection) -> None:
    definition = await conn.fetchval(
        "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE conname = 'item_events_kind_valid'"
    )

    stored = {kind for kind in enums.EventKind if f"'{kind.value}'" in definition}
    assert stored == set(enums.EventKind)
    assert definition.count("::text") == len(enums.EventKind)  # nothing extra in the list


async def test_schema_module_covers_every_table(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables "
        "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
    )

    assert {row["tablename"] for row in rows} == set(schema.metadata.tables)


@pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
async def test_columns_match_the_database(conn: asyncpg.Connection, table: sa.Table) -> None:
    rows = await conn.fetch(
        "SELECT column_name, is_nullable, column_default, is_generated, identity_generation "
        "FROM information_schema.columns WHERE table_schema = 'public' AND table_name = $1",
        table.name,
    )
    actual = {row["column_name"]: row for row in rows}

    assert set(actual) == {c.name for c in table.columns}
    for column in table.columns:
        row = actual[column.name]
        assert (row["is_nullable"] == "YES") == bool(column.nullable), (
            f"{table.name}.{column.name} nullability"
        )
        assert (row["is_generated"] == "ALWAYS") == (column.computed is not None), (
            f"{table.name}.{column.name} generated"
        )
        assert (row["identity_generation"] == "ALWAYS") == (column.identity is not None), (
            f"{table.name}.{column.name} identity"
        )
        has_default = row["column_default"] is not None
        expected_default = column.server_default is not None and column.computed is None
        assert has_default == expected_default or column.identity is not None, (
            f"{table.name}.{column.name} default"
        )


@pytest.mark.parametrize("table", _tables(), ids=lambda t: t.name)
async def test_primary_and_foreign_keys_match_the_database(
    conn: asyncpg.Connection, table: sa.Table
) -> None:
    pk = await conn.fetch(
        "SELECT a.attname FROM pg_index i "
        "JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey) "
        "WHERE i.indrelid = $1::regclass AND i.indisprimary",
        f"public.{table.name}",
    )
    fks = await conn.fetch(
        "SELECT a.attname, c.confrelid::regclass::text AS target FROM pg_constraint c "
        "JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY (c.conkey) "
        "WHERE c.conrelid = $1::regclass AND c.contype = 'f'",
        f"public.{table.name}",
    )

    assert {row["attname"] for row in pk} == {c.name for c in table.primary_key.columns}
    declared = {
        (fk.parent.name, fk.column.table.name)
        for column in table.columns
        for fk in column.foreign_keys
    }
    assert {(row["attname"], row["target"]) for row in fks} == declared
