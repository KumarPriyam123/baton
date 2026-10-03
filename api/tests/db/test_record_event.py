"""CLAUDE.md I1: record_event writes event, stamp, outbox row and NOTIFY together."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db import tx
from app.db.urls import asyncpg_dsn
from app.domain.enums import EventKind
from app.repo.items import ChangedItem
from tests.support.db import scratch_database

from .factories import World, make_world

LONG_AGO = datetime(2025, 1, 1, tzinfo=UTC)
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
        await conn.execute("TRUNCATE outbox")
        yield await make_world(conn)
    finally:
        await conn.close()


def subject(world: World, **overrides: Any) -> ChangedItem:
    values: dict[str, Any] = {
        "id": world.item_id,
        "key": world.item_key,
        "team_id": world.team.id,
        "version": 4,
        "requester_id": world.requester,
        "assignee_id": world.member,
        "confidential": False,
    }
    values.update(overrides)
    return ChangedItem(**values)


async def fetch(url: str, sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return await conn.fetch(sql, *args)
    finally:
        await conn.close()


async def stale_the_item(url: str, world: World) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        await conn.execute(
            "UPDATE work_items SET last_activity_at = $2, updated_at = $2 WHERE id = $1",
            world.item_id,
            LONG_AGO,
        )
    finally:
        await conn.close()


async def test_the_event_carries_the_items_version_actor_request_and_time(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    async def work(command: tx.CommandTx) -> int:
        return await tx.record_event(
            command,
            subject(world),
            EventKind.FIELD_CHANGED,
            data={"title": {"from": "a", "to": "b"}},
            reason="because",
            is_decision=True,
        )

    async with engine.connect() as conn:
        event_id = await tx.run_command(
            conn, work, actor_id=world.lead, request_id="req-1", now=NOW
        )

    (event,) = await fetch(scratch_url, "SELECT * FROM item_events WHERE id = $1", event_id)
    assert (event["item_version"], event["actor_id"], event["request_id"]) == (
        4,
        world.lead,
        "req-1",
    )
    assert (event["kind"], event["reason"], event["is_decision"]) == (
        "field_changed",
        "because",
        True,
    )
    assert json.loads(event["data"]) == {"title": {"from": "a", "to": "b"}}
    assert event["team_id"] == world.team.id
    assert event["created_at"] == NOW


async def test_it_stamps_the_item_and_a_person_restarts_the_stale_clock(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    await stale_the_item(scratch_url, world)

    async def work(command: tx.CommandTx) -> int:
        return await tx.record_event(command, subject(world), EventKind.FIELD_CHANGED)

    async with engine.connect() as conn:
        event_id = await tx.run_command(conn, work, actor_id=world.lead, now=NOW)

    (item,) = await fetch(
        scratch_url,
        "SELECT last_event_id, updated_at, last_activity_at FROM work_items WHERE id = $1",
        world.item_id,
    )
    assert (item["last_event_id"], item["updated_at"], item["last_activity_at"]) == (
        event_id,
        NOW,
        NOW,
    )


async def test_a_system_event_stamps_the_item_but_never_resets_going_stale(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    await stale_the_item(scratch_url, world)

    async def work(command: tx.CommandTx) -> int:
        return await tx.record_event(command, subject(world), EventKind.SLA_BREACHED)

    async with engine.connect() as conn:
        event_id = await tx.run_command(conn, work, actor_id=None, now=NOW)  # the worker

    (item,) = await fetch(
        scratch_url,
        "SELECT last_event_id, updated_at, last_activity_at FROM work_items WHERE id = $1",
        world.item_id,
    )
    (event,) = await fetch(scratch_url, "SELECT actor_id FROM item_events WHERE id = $1", event_id)
    assert event["actor_id"] is None
    assert item["last_event_id"] == event_id
    assert item["updated_at"] == NOW  # every event sets updated_at (SPEC 6.2)
    assert item["last_activity_at"] == LONG_AGO  # a machine-written event must not reset it


async def test_it_enqueues_one_outbox_row_for_the_event(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    async def work(command: tx.CommandTx) -> int:
        return await tx.record_event(command, subject(world), EventKind.CREATED)

    async with engine.connect() as conn:
        event_id = await tx.run_command(conn, work, actor_id=world.lead, request_id="req-9")

    (row,) = await fetch(scratch_url, "SELECT topic, payload, status FROM outbox")
    assert row["topic"] == "item.event"
    assert json.loads(row["payload"]) == {"event_ids": [event_id], "request_id": "req-9"}
    assert row["status"] == "pending"


async def test_it_notifies_listeners_with_the_spec_payload_only_after_commit(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    listener = await asyncpg.connect(asyncpg_dsn(scratch_url))
    received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    await listener.add_listener("item_changes", lambda *a: received.put_nowait(json.loads(a[3])))
    try:

        async def work(command: tx.CommandTx) -> int:
            event_id = await tx.record_event(
                command,
                subject(world, version=5, assignee_id=None, confidential=True),
                EventKind.FIELD_CHANGED,
            )
            await asyncio.sleep(0.2)  # a window in which the transaction is still open
            assert received.empty(), "NOTIFY must not be delivered before COMMIT"
            return event_id

        async with engine.connect() as conn:
            event_id = await tx.run_command(conn, work, actor_id=world.lead)
        notice = await asyncio.wait_for(received.get(), timeout=5)
    finally:
        await listener.close()

    assert notice == {
        "event_id": event_id,
        "item_id": str(world.item_id),
        "version": 5,
        "team_id": str(world.team.id),
        "prev_team_id": None,
        "requester_id": str(world.requester),
        "assignee_id": None,
        "confidential": True,
    }


async def test_a_command_that_fails_after_recording_leaves_no_event_outbox_row_or_notice(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    before_events = await fetch(scratch_url, "SELECT count(*) AS n FROM item_events")
    listener = await asyncpg.connect(asyncpg_dsn(scratch_url))
    received: list[str] = []
    await listener.add_listener("item_changes", lambda *a: received.append(a[3]))

    class Boom(Exception):
        pass

    async def work(command: tx.CommandTx) -> None:
        await tx.record_event(command, subject(world), EventKind.FIELD_CHANGED)
        raise Boom

    try:
        async with engine.connect() as conn:
            with pytest.raises(Boom):
                await tx.run_command(conn, work, actor_id=world.lead)
        await asyncio.sleep(0.2)
    finally:
        await listener.close()

    assert received == []
    assert await fetch(scratch_url, "SELECT count(*) AS n FROM item_events") == before_events
    assert await fetch(scratch_url, "SELECT * FROM outbox") == []
    (item,) = await fetch(
        scratch_url, "SELECT last_event_id FROM work_items WHERE id = $1", world.item_id
    )
    assert (
        item["last_event_id"] is None
    )  # the factory never stamped it, and neither did the rolled-back command


async def test_the_event_kind_must_be_one_the_schema_knows(
    engine: AsyncEngine, world: World
) -> None:
    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(
            sa.text(
                "INSERT INTO item_events (item_id, team_id, kind, item_version) "
                "VALUES (:i, :t, 'invented', 1)"
            ),
            {"i": world.item_id, "t": world.team.id},
        )

    async with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):  # item_events_kind_valid: a bug, not a 4xx
            await tx.run_command(conn, work)
