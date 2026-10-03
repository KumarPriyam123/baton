"""SPEC 6.3 at the database level: what the key row does when the command fails in each way.

These close gaps the phase 3 review found: the retry loop through the idempotency layer and
record_event together, the DBAPIError branch, and a task cancelled in the middle of a command.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from typing import Any

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.api import idempotency
from app.api.idempotency import Outcome, run_idempotent
from app.db import tx
from app.db.urls import asyncpg_dsn
from app.domain.enums import EventKind
from app.domain.errors import Busy
from app.repo.items import ChangedItem
from tests.support.db import scratch_database

from .factories import World, make_world

FP = "fingerprint-a"


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
        await conn.execute(
            "TRUNCATE users, teams, memberships, work_items, item_events, outbox, "
            "idempotency_keys CASCADE"
        )
        yield await make_world(conn)
    finally:
        await conn.close()


async def count(url: str, table: str, where: str = "TRUE") -> int:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return int(await conn.fetchval(f"SELECT count(*) FROM {table} WHERE {where}"))  # noqa: S608
    finally:
        await conn.close()


def item_of(world: World) -> ChangedItem:
    return ChangedItem(
        id=world.item_id,
        key=world.item_key,
        team_id=world.team.id,
        version=1,
        requester_id=world.requester,
        assignee_id=None,
        confidential=False,
    )


def run(engine: AsyncEngine, world: World, key: str, work: idempotency.Work, fp: str = FP) -> Any:
    async def go() -> Outcome:
        async with engine.connect() as conn:
            return await run_idempotent(
                conn,
                actor_id=world.lead,
                request_id="req-x",
                key=key,
                request_fingerprint=fp,
                work=work,
            )

    return go()


FORCE_40001 = sa.text("DO $$ BEGIN RAISE EXCEPTION 'forced' USING ERRCODE = '40001'; END $$")


async def test_a_retry_after_40001_inside_the_command_writes_the_key_event_and_outbox_once(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    attempts = 0
    events_before = await count(scratch_url, "item_events")

    async def work(command: tx.CommandTx) -> Outcome:
        nonlocal attempts
        attempts += 1
        await tx.record_event(command, item_of(world), EventKind.FIELD_CHANGED, data={"n": 1})
        assert len(command.event_ids) == 1  # state is per attempt, not carried over
        if attempts < 3:
            await command.conn.execute(FORCE_40001)
        return Outcome(200, {"attempts": attempts})

    outcome = await run(engine, world, "k-retry", work)

    assert (outcome.status, outcome.body, outcome.replayed) == (200, {"attempts": 3}, False)
    assert attempts == 3
    assert await count(scratch_url, "idempotency_keys", "key = 'k-retry'") == 1
    assert await count(scratch_url, "item_events") == events_before + 1
    assert await count(scratch_url, "outbox") == 1
    replay = await run(engine, world, "k-retry", work)
    assert (replay.replayed, replay.body) == (True, {"attempts": 3})
    assert attempts == 3  # the replay did not run the command


async def test_a_constraint_that_fires_inside_the_command_is_stored_as_a_4xx_and_replayed(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    runs = 0

    async def work(command: tx.CommandTx) -> Outcome:
        nonlocal runs
        runs += 1
        await tx.record_event(command, item_of(world), EventKind.FIELD_CHANGED)
        await command.conn.execute(
            sa.text("UPDATE work_items SET priority = 9 WHERE id = :id"), {"id": world.item_id}
        )
        return Outcome(200, {})

    first = await run(engine, world, "k-constraint", work)
    second = await run(engine, world, "k-constraint", work)

    assert (first.status, first.body["code"]) == (400, "VALIDATION_FAILED")
    assert (second.status, second.replayed, second.body) == (400, True, first.body)
    assert runs == 1
    # the savepoint took the event and the outbox row of the failed attempt with it
    assert await count(scratch_url, "item_events") == 1
    assert await count(scratch_url, "outbox") == 0


async def test_a_lock_timeout_inside_the_command_is_busy_and_not_stored(
    engine: AsyncEngine, world: World, scratch_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tx, "LOCK_TIMEOUT", "150ms")
    holder = await asyncpg.connect(asyncpg_dsn(scratch_url))
    attempts = 0

    async def work(command: tx.CommandTx) -> Outcome:
        nonlocal attempts
        attempts += 1
        await command.conn.execute(
            sa.text("SELECT 1 FROM work_items WHERE id = :id FOR UPDATE"), {"id": world.item_id}
        )
        return Outcome(200, {"ok": True})

    try:
        async with holder.transaction():
            await holder.execute("SELECT 1 FROM work_items WHERE id = $1 FOR UPDATE", world.item_id)
            with pytest.raises(Busy):
                await run(engine, world, "k-busy", work)
            assert await count(scratch_url, "idempotency_keys", "key = 'k-busy'") == 0
    finally:
        await holder.close()

    retry = await run(engine, world, "k-busy", work)  # the lock is free now

    assert (retry.status, retry.replayed) == (200, False)
    assert attempts == 2


async def test_an_error_nobody_mapped_rolls_the_key_back_too(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    async def work(command: tx.CommandTx) -> Outcome:
        await command.conn.execute(
            sa.text(
                "INSERT INTO item_events (item_id, team_id, kind, item_version) "
                "VALUES (:i, :t, 'invented', 1)"
            ),
            {"i": world.item_id, "t": world.team.id},
        )
        return Outcome(200, {})

    with pytest.raises(sa.exc.IntegrityError):
        await run(engine, world, "k-bug", work)

    assert await count(scratch_url, "idempotency_keys", "key = 'k-bug'") == 0


async def test_a_task_cancelled_mid_command_leaves_no_key_event_or_outbox_and_the_pool_is_clean(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    started = asyncio.Event()

    async def work(command: tx.CommandTx) -> Outcome:
        await tx.record_event(command, item_of(world), EventKind.FIELD_CHANGED)
        started.set()
        await command.conn.execute(sa.text("SELECT pg_sleep(30)"))
        return Outcome(200, {})

    task = asyncio.create_task(run(engine, world, "k-cancel", work))
    await asyncio.wait_for(started.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await count(scratch_url, "idempotency_keys", "key = 'k-cancel'") == 0
    assert await count(scratch_url, "item_events") == 1  # the factory's
    assert await count(scratch_url, "outbox") == 0

    async def quick(command: tx.CommandTx) -> Outcome:
        shown = (await command.conn.execute(sa.text("SHOW lock_timeout"))).scalar_one()
        return Outcome(200, {"lock_timeout": shown})

    again = await run(engine, world, "k-cancel", quick)  # same key, same pool, fresh run
    assert (again.status, again.replayed, json.dumps(again.body)) == (
        200,
        False,
        '{"lock_timeout": "2s"}',
    )
