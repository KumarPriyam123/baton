"""The WHERE clauses of the two conditional UPDATEs are the database's own guard (SPEC 6.1, 6.2).

The commands also check the same facts in Python under the row lock, which is why a missing clause
would not show in a request-level test. These call the repo functions directly, so each clause is
tested by itself: take one out and exactly one of these goes red.
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.urls import asyncpg_dsn
from app.repo import items as items_repo
from tests.support.db import scratch_database

from .factories import World, make_world

NOW = datetime(2025, 6, 1, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def scratch_url(test_database_url: str) -> Iterator[str]:
    with scratch_database(test_database_url) as url:
        yield url


@pytest.fixture
async def engine(scratch_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(scratch_url, pool_size=3)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def world(scratch_url: str) -> AsyncIterator[World]:
    conn = await asyncpg.connect(asyncpg_dsn(scratch_url))
    try:
        await conn.execute("TRUNCATE users, teams, memberships, work_items, item_events CASCADE")
        made = await make_world(conn)
        await conn.execute("UPDATE work_items SET version = 4")  # the item has been edited
        yield made
    finally:
        await conn.close()


async def stored(url: str, item_id: uuid.UUID) -> asyncpg.Record:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        found = await conn.fetchrow("SELECT * FROM work_items WHERE id = $1", item_id)
        assert found is not None
        return found
    finally:
        await conn.close()


async def set_columns(url: str, item_id: uuid.UUID, **columns: Any) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        sets = ", ".join(f"{name} = ${n}" for n, name in enumerate(columns, start=2))
        await conn.execute(
            f"UPDATE work_items SET {sets} WHERE id = $1",  # noqa: S608  (test code names columns)
            item_id,
            *columns.values(),
        )
    finally:
        await conn.close()


# ----- update_item: version = :expected -------------------------------------------------------


async def test_an_update_on_a_stale_version_writes_nothing(
    engine: AsyncEngine,
    world: World,
    scratch_url: str,
) -> None:
    async with engine.begin() as conn:
        result = await items_repo.update_item(
            conn, world.item_id, expected_version=3, values={"title": "Too late"}, now=NOW
        )

    assert result is None
    row = await stored(scratch_url, world.item_id)
    assert row["version"] == 4
    assert row["title"] != "Too late"


async def test_an_update_on_the_current_version_bumps_it_by_exactly_one(
    engine: AsyncEngine,
    world: World,
    scratch_url: str,
) -> None:
    async with engine.begin() as conn:
        result = await items_repo.update_item(
            conn, world.item_id, expected_version=4, values={"title": "In time"}, now=NOW
        )

    assert result is not None
    assert result.version == 5
    assert (await stored(scratch_url, world.item_id))["title"] == "In time"


# ----- claim_item: assignee_id IS NULL AND status = 'new' -------------------------------------


async def test_a_claim_on_an_item_that_has_an_owner_matches_nothing_even_if_it_says_new(
    engine: AsyncEngine,
    world: World,
    scratch_url: str,
) -> None:
    await set_columns(scratch_url, world.item_id, status="new", assignee_id=world.member)

    async with engine.begin() as conn:
        result = await items_repo.claim_item(
            conn, world.item_id, {"assignee_id": world.lead, "status": "in_progress"}, NOW
        )

    assert result is None
    assert (await stored(scratch_url, world.item_id))["assignee_id"] == world.member


async def test_a_claim_on_an_item_that_is_not_new_matches_nothing(
    engine: AsyncEngine,
    world: World,
    scratch_url: str,
) -> None:
    await set_columns(scratch_url, world.item_id, status="resolved", resolution="done")
    await set_columns(scratch_url, world.item_id, assignee_id=None)

    async with engine.begin() as conn:
        result = await items_repo.claim_item(
            conn, world.item_id, {"assignee_id": world.lead, "status": "in_progress"}, NOW
        )

    assert result is None
    assert (await stored(scratch_url, world.item_id))["assignee_id"] is None


async def test_a_claim_on_an_unowned_new_item_takes_it_and_bumps_the_version_once(
    engine: AsyncEngine,
    world: World,
    scratch_url: str,
) -> None:
    await set_columns(scratch_url, world.item_id, status="new", assignee_id=None)

    async with engine.begin() as conn:
        result = await items_repo.claim_item(
            conn, world.item_id, {"assignee_id": world.lead, "status": "in_progress"}, NOW
        )

    assert result is not None
    assert (result.version, result.assignee_id) == (5, world.lead)
    row = await stored(scratch_url, world.item_id)
    assert (row["status"], row["assignee_id"]) == ("in_progress", world.lead)
