"""SPEC 3.2 inventory: the tables, primary keys and every named index exist as specified."""

import re
from typing import Any

import asyncpg
import pytest

# One row per index in the SPEC 3.2 tables: name -> (table, method, key, tokens the predicate has).
# `serves` is the query the SPEC says the index exists for.
EXPECTED_INDEXES: dict[str, dict[str, Any]] = {
    "work_items_team_queue_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "team_id, status, priority, due_at, id",
        "serves": "team queue, default sort",
    },
    "work_items_team_updated_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "team_id, updated_at DESC, id DESC",
        "serves": "recently updated sort",
    },
    "work_items_assignee_open_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "assignee_id, priority, due_at",
        "predicate": ["resolved", "closed"],
        "serves": "assigned to me",
    },
    "work_items_needs_owner_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "team_id, priority, created_at",
        "predicate": ["assignee_id IS NULL", "status = 'new'"],
        "serves": "needs an owner",
    },
    "work_items_requester_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "requester_id, updated_at DESC",
        "serves": "my requests",
    },
    "work_items_sla_sweep_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "due_at",
        "predicate": ["resolved", "closed", "sla_breached_at IS NULL"],
        "serves": "SLA sweep",
    },
    "work_items_going_stale_idx": {
        "table": "work_items",
        "method": "btree",
        "key": "team_id, last_activity_at",
        "predicate": ["in_progress", "blocked"],
        "serves": "going stale",
    },
    "work_items_search_idx": {
        "table": "work_items",
        "method": "gin",
        "key": "search",
        "serves": "full-text search",
    },
    "work_items_title_trgm_idx": {
        "table": "work_items",
        "method": "gin",
        "key": "title gin_trgm_ops",
        "serves": "fuzzy title search and duplicate suggestions",
    },
    "item_events_item_idx": {
        "table": "item_events",
        "method": "btree",
        "key": "item_id, id DESC",
        "serves": "timeline",
    },
    "item_events_decisions_idx": {
        "table": "item_events",
        "method": "btree",
        "key": "team_id, id DESC",
        "predicate": ["is_decision"],
        "serves": "decision log",
    },
    "item_events_created_brin": {
        "table": "item_events",
        "method": "brin",
        "key": "created_at",
        "serves": "time ranges",
    },
    "memberships_user_id_idx": {
        "table": "memberships",
        "method": "btree",
        "key": "user_id",
        "serves": "a user's teams",
    },
    "approvals_one_pending": {
        "table": "approvals",
        "method": "btree",
        "key": "item_id",
        "unique": True,
        "predicate": ["pending"],
        "serves": "one open approval request per item",
    },
    "notifications_unread_idx": {
        "table": "notifications",
        "method": "btree",
        "key": "user_id, id DESC",
        "predicate": ["read_at IS NULL"],
        "serves": "unread notifications",
    },
    "idempotency_keys_created_idx": {
        "table": "idempotency_keys",
        "method": "btree",
        "key": "created_at",
        "serves": "cleanup",
    },
    "outbox_pending_idx": {
        "table": "outbox",
        "method": "btree",
        "key": "available_at, id",
        "predicate": ["pending"],
        "serves": "worker claim",
    },
}

EXPECTED_PRIMARY_KEYS = {
    "users": "id",
    "teams": "id",
    "memberships": "team_id, user_id",
    "work_items": "id",
    "item_events": "id",
    "comments": "id",
    "approvals": "id",
    "watchers": "item_id, user_id",
    "item_reads": "user_id, item_id",
    "notifications": "id",
    "item_similar": "item_id, similar_item_id",
    "idempotency_keys": "user_id, key",
    "outbox": "id",
    "sessions": "id",
}


def _strip_casts(text: str) -> str:
    return re.sub(r"::[a-z_ ]+(\[\])?", "", text)


async def _index_definitions(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"
    )
    return {row["indexname"]: row["indexdef"] for row in rows}


async def test_every_spec_table_exists_and_nothing_else(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
    )

    assert {row["table_name"] for row in rows} == set(EXPECTED_PRIMARY_KEYS) | {"alembic_version"}


@pytest.mark.parametrize("table", sorted(EXPECTED_PRIMARY_KEYS))
async def test_primary_key_matches_the_spec(conn: asyncpg.Connection, table: str) -> None:
    columns = await conn.fetch(
        """
        SELECT a.attname FROM pg_index i
          JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
         WHERE i.indrelid = $1::regclass AND i.indisprimary
         ORDER BY array_position(i.indkey::int2[], a.attnum)""",
        f"public.{table}",
    )

    assert ", ".join(row["attname"] for row in columns) == EXPECTED_PRIMARY_KEYS[table]


@pytest.mark.parametrize("name", sorted(EXPECTED_INDEXES))
async def test_index_exists_as_specified(conn: asyncpg.Connection, name: str) -> None:
    expected = EXPECTED_INDEXES[name]
    definitions = await _index_definitions(conn)

    assert name in definitions, f"missing index {name} (serves: {expected['serves']})"
    definition = _strip_casts(definitions[name])
    assert f"ON public.{expected['table']} USING {expected['method']} (" in definition
    assert f"({expected['key']})" in definition
    assert definition.startswith("CREATE UNIQUE INDEX") == bool(expected.get("unique"))
    predicate = expected.get("predicate", [])
    if predicate:
        assert " WHERE " in definition
        where = definition.split(" WHERE ", 1)[1]
        for token in predicate:
            assert token in where, f"{name}: predicate {where!r} lacks {token!r}"
    else:
        assert " WHERE " not in definition, f"{name} should not be partial"


async def test_no_index_exists_without_a_reason(conn: asyncpg.Connection) -> None:
    """Every non-constraint index is either in the SPEC table or fails this test."""
    rows = await conn.fetch(
        """
        SELECT c.relname FROM pg_index i
          JOIN pg_class c ON c.oid = i.indexrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = 'public'
           AND NOT EXISTS (SELECT 1 FROM pg_constraint k WHERE k.conindid = i.indexrelid)"""
    )

    assert {row["relname"] for row in rows} == set(EXPECTED_INDEXES)
