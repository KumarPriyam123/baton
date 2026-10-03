"""SPEC 6.4: the command transaction. Real Postgres, real connections, committed data."""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

import asyncpg
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db import errors, tx
from app.db.urls import asyncpg_dsn
from app.domain.errors import Busy, ValidationFailed, WorkflowViolation
from tests.support.db import scratch_database

from .factories import World, make_item, make_world


@pytest.fixture(scope="module")
def scratch_url(test_database_url: str) -> Iterator[str]:
    with scratch_database(test_database_url) as url:
        yield url


@pytest.fixture
async def engine(scratch_url: str) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(scratch_url, pool_size=5)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def world(scratch_url: str) -> AsyncIterator[World]:
    conn = await asyncpg.connect(asyncpg_dsn(scratch_url))
    try:
        # A fresh universe per test; team keys must be unique, so use the factory's own.
        await conn.execute("TRUNCATE users, teams, memberships, work_items, item_events CASCADE")
        yield await make_world(conn)
    finally:
        await conn.close()


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return int((await conn.execute(sa.text(f"SELECT count(*) FROM {table}"))).scalar_one())  # noqa: S608


async def test_command_tx_sets_tight_timeouts_for_this_transaction_only(
    engine: AsyncEngine,
) -> None:
    async with engine.connect() as conn:
        async with tx.command_tx(conn):
            lock = (await conn.execute(sa.text("SHOW lock_timeout"))).scalar_one()
            statement = (await conn.execute(sa.text("SHOW statement_timeout"))).scalar_one()
        after = (await conn.execute(sa.text("SHOW lock_timeout"))).scalar_one()
    assert (lock, statement) == ("2s", "5s")
    assert after == "0"  # SET LOCAL ended with the transaction


async def test_command_tx_commits_when_the_body_returns(engine: AsyncEngine, world: World) -> None:
    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(
            sa.text("UPDATE work_items SET title = 'Renamed by test' WHERE id = :id"),
            {"id": world.item_id},
        )

    async with engine.connect() as conn:
        await tx.run_command(conn, work)
    async with engine.connect() as conn:
        title = (
            await conn.execute(
                sa.text("SELECT title FROM work_items WHERE id = :id"), {"id": world.item_id}
            )
        ).scalar_one()
    assert title == "Renamed by test"


async def test_command_tx_rolls_back_everything_when_the_body_raises(
    engine: AsyncEngine, world: World
) -> None:
    class Boom(Exception):
        pass

    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(
            sa.text("UPDATE work_items SET title = 'Never visible' WHERE id = :id"),
            {"id": world.item_id},
        )
        raise Boom

    async with engine.connect() as conn:
        with pytest.raises(Boom):
            await tx.run_command(conn, work)
    async with engine.connect() as conn:
        title = (
            await conn.execute(
                sa.text("SELECT title FROM work_items WHERE id = :id"), {"id": world.item_id}
            )
        ).scalar_one()
    assert title != "Never visible"


async def test_waiting_for_a_row_lock_too_long_is_503_busy_with_retry_after(
    engine: AsyncEngine, world: World, scratch_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tx, "LOCK_TIMEOUT", "150ms")
    holder = await asyncpg.connect(asyncpg_dsn(scratch_url))
    try:
        async with holder.transaction():
            await holder.execute("SELECT 1 FROM work_items WHERE id = $1 FOR UPDATE", world.item_id)

            async def work(command: tx.CommandTx) -> None:
                await command.conn.execute(
                    sa.text("SELECT 1 FROM work_items WHERE id = :id FOR UPDATE"),
                    {"id": world.item_id},
                )

            async with engine.connect() as conn:
                with pytest.raises(Busy) as caught:
                    await tx.run_command(conn, work)
    finally:
        await holder.close()
    assert caught.value.status == 503
    assert caught.value.headers == {"Retry-After": "1"}


async def test_a_statement_running_too_long_is_503_busy(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tx, "STATEMENT_TIMEOUT", "100ms")

    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(sa.text("SELECT pg_sleep(2)"))

    async with engine.connect() as conn:
        with pytest.raises(Busy):
            await tx.run_command(conn, work)


_RAISE_40001 = sa.text(
    "DO $$ BEGIN RAISE EXCEPTION 'forced' USING ERRCODE = '40001'; END $$"  # serialization failure
)


async def test_serialization_failure_restarts_the_whole_command_and_drops_the_first_attempts(
    engine: AsyncEngine, world: World
) -> None:
    attempts = 0

    async def work(command: tx.CommandTx) -> int:
        nonlocal attempts
        attempts += 1
        await command.conn.execute(
            sa.text(
                "INSERT INTO comments (item_id, author_id, body) VALUES (:item, :author, 'once')"
            ),
            {"item": world.item_id, "author": world.requester},
        )
        if attempts < 3:
            await command.conn.execute(_RAISE_40001)
        return attempts

    async with engine.connect() as conn:
        assert await tx.run_command(conn, work) == 3
    assert await _count(engine, "comments") == 1  # the two failed attempts left nothing behind


async def test_a_command_that_keeps_failing_with_40001_gives_up_after_three_attempts_as_busy(
    engine: AsyncEngine, world: World
) -> None:
    attempts = 0

    async def work(command: tx.CommandTx) -> None:
        nonlocal attempts
        attempts += 1
        await command.conn.execute(_RAISE_40001)

    async with engine.connect() as conn:
        with pytest.raises(Busy):
            await tx.run_command(conn, work)
    assert attempts == tx.MAX_ATTEMPTS == 3


async def test_a_real_deadlock_is_retried_and_both_commands_succeed(
    engine: AsyncEngine, world: World, scratch_url: str
) -> None:
    """Two commands lock the same two rows in opposite order. Postgres kills one with 40P01; it
    is run again from the start and the pair finishes."""
    admin = await asyncpg.connect(asyncpg_dsn(scratch_url))
    try:
        other = await make_item(admin, world.team, world.requester)
    finally:
        await admin.close()
    first, second = world.item_id, other["id"]
    both_hold_one = asyncio.Barrier(2)
    attempts: dict[str, int] = {"a": 0, "b": 0}

    def command(name: str, one: object, two: object) -> Callable[[tx.CommandTx], Awaitable[None]]:
        async def work(command: tx.CommandTx) -> None:
            attempts[name] += 1
            lock = sa.text("SELECT 1 FROM work_items WHERE id = :id FOR UPDATE")
            await command.conn.execute(lock, {"id": one})
            if attempts[name] == 1:
                await both_hold_one.wait()  # make the opposite-order collision certain
            await command.conn.execute(lock, {"id": two})

        return work

    async def run(name: str, one: object, two: object) -> None:
        async with engine.connect() as conn:
            await tx.run_command(conn, command(name, one, two))

    await asyncio.gather(run("a", first, second), run("b", second, first))
    assert sorted(attempts.values()) == [1, 2]  # exactly one of them was the deadlock victim


async def test_a_violated_check_constraint_becomes_the_domain_error_not_a_500(
    engine: AsyncEngine, world: World
) -> None:
    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(
            sa.text("UPDATE work_items SET status = 'in_progress' WHERE id = :id"),
            {"id": world.item_id},  # no assignee: owner_when_active
        )

    async with engine.connect() as conn:
        with pytest.raises(WorkflowViolation) as caught:
            await tx.run_command(conn, work)
    assert caught.value.code == "WORKFLOW_VIOLATION"


async def test_a_constraint_with_a_field_names_the_field(engine: AsyncEngine, world: World) -> None:
    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(
            sa.text("UPDATE work_items SET priority = 9 WHERE id = :id"), {"id": world.item_id}
        )

    async with engine.connect() as conn:
        with pytest.raises(ValidationFailed) as caught:
            await tx.run_command(conn, work)
    assert caught.value.errors is not None
    assert caught.value.errors[0]["field"] == "priority"


async def test_a_constraint_nobody_mapped_stays_an_error_instead_of_being_hidden(
    engine: AsyncEngine, world: World
) -> None:
    async def work(command: tx.CommandTx) -> None:
        await command.conn.execute(
            sa.text("INSERT INTO teams (key, name) VALUES ('payments', 'bad key')")
        )

    async with engine.connect() as conn:
        with pytest.raises(sa.exc.IntegrityError):  # teams_key_format is a bug if it ever fires
            await tx.run_command(conn, work)


# Constraints that deliberately have no friendly error: if one fires, the code is wrong (500).
# Everything else in the schema must be listed in errors.CONSTRAINT_ERRORS.
LEFT_AS_BUGS = {
    "teams_key_format",  # the router upper-cases and validates the key
    "item_events_kind_valid",  # kinds come from the EventKind enum
}


async def test_every_check_and_partial_unique_index_is_either_mapped_or_a_declared_bug(
    scratch_url: str,
) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(scratch_url))
    try:
        checks = await conn.fetch(
            "SELECT conname FROM pg_constraint WHERE contype = 'c' "
            "AND connamespace = 'public'::regnamespace"
        )
        partial_unique = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = 'public' "
            "AND indexdef LIKE 'CREATE UNIQUE INDEX%' AND indexdef LIKE '%WHERE%'"
        )
    finally:
        await conn.close()
    names = {r["conname"] for r in checks} | {r["indexname"] for r in partial_unique}
    unmapped = names - set(errors.CONSTRAINT_ERRORS) - LEFT_AS_BUGS
    assert not unmapped, f"add these to db/errors.py (or LEFT_AS_BUGS with a reason): {unmapped}"
    stale = set(errors.CONSTRAINT_ERRORS) - names - {"memberships_pkey"}
    assert not stale, f"mapped but not in the schema: {stale}"
