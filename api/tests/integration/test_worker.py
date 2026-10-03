"""The outbox worker (SPEC 9): each test names the failure it guards. Real Postgres, real runners;
the clock is injected, so nothing sleeps for a lease or a backoff."""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.db.engine import create_engine
from app.db.tx import CommandTx
from app.worker.runner import Runner
from app.worker.schedules import sla_sweep
from tests.integration.conftest import csrf_headers
from tests.integration.test_teams_members import signed_in
from tests.support.items import created, new_key, rows, scalar, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.users import execute
from tests.support.workflow import ADMIN, ASHA, ISHAAN, MEERA, act, claimed, ok

FARAH = "farah@baton.test"  # member of CMP, not a lead


@pytest.fixture
async def engine(app_settings: Settings, seeded: SeededDatabase) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(app_settings)
    await execute(seeded.url, "DELETE FROM outbox")  # the seed and earlier tests leave jobs behind
    await execute(
        seeded.url, "CREATE TABLE IF NOT EXISTS effects (job integer NOT NULL, runner text)"
    )
    await execute(seeded.url, "TRUNCATE effects")
    yield engine
    await engine.dispose()


async def enqueue(url: str, topic: str, count: int = 1, payload: str = "{}") -> list[int]:
    found = await rows(
        url,
        "INSERT INTO outbox (topic, payload) SELECT $1, $2::jsonb || jsonb_build_object('n', n) "
        "FROM generate_series(1, $3) AS n RETURNING id",
        topic,
        payload,
        count,
    )
    return [r["id"] for r in found]


def effect(runner: str) -> Callable[[CommandTx, dict[str, Any]], Any]:
    async def handle(tx: CommandTx, payload: dict[str, Any]) -> None:
        await tx.conn.execute(
            sa.text("INSERT INTO effects (job, runner) VALUES (:job, :runner)"),
            {"job": payload["n"], "runner": runner},
        )

    return handle


async def drain(runner: Runner) -> None:
    while await runner.run_once():
        pass


async def job(url: str, job_id: int) -> dict[str, Any]:
    return (await rows(url, "SELECT * FROM outbox WHERE id = $1", job_id))[0]


# ----- T-OUTBOX ---------------------------------------------------------------------------------


async def test_two_runners_against_500_jobs_do_each_side_effect_exactly_once(
    engine: AsyncEngine, seeded: SeededDatabase
) -> None:
    await enqueue(seeded.url, "test.effect", 500)
    one = Runner(engine, {"test.effect": effect("one")}, batch=20)
    two = Runner(engine, {"test.effect": effect("two")}, batch=20)

    await asyncio.gather(drain(one), drain(two))

    done = await rows(seeded.url, "SELECT job, count(*) AS n FROM effects GROUP BY job")
    assert len(done) == 500
    assert {r["n"] for r in done} == {1}  # never twice: SKIP LOCKED hands each job to one runner
    assert await scalar(seeded.url, "SELECT count(*) FROM outbox WHERE status <> 'done'") == 0
    assert (
        await scalar(seeded.url, "SELECT count(DISTINCT runner) FROM effects") == 2
    )  # both worked


# ----- a crashed or hung runner -----------------------------------------------------------------


async def test_a_job_held_by_a_dead_runner_is_taken_over_after_the_lease_and_runs_once(
    engine: AsyncEngine, seeded: SeededDatabase
) -> None:
    (job_id,) = await enqueue(seeded.url, "test.effect")
    started = asyncio.Event()

    async def hang(tx: CommandTx, payload: dict[str, Any]) -> None:
        started.set()
        await asyncio.Event().wait()  # never returns: the process "dies" here

    crashing = asyncio.create_task(Runner(engine, {"test.effect": hang}).run_once())
    await asyncio.wait_for(started.wait(), timeout=10)
    crashing.cancel()  # a kill, not a failure: the lease stays
    with contextlib.suppress(asyncio.CancelledError):
        await crashing
    held = await job(seeded.url, job_id)

    during = await Runner(engine, {"test.effect": effect("early")}).run_once()  # lease still valid
    later = datetime.now(UTC) + timedelta(seconds=61)
    after = Runner(engine, {"test.effect": effect("takeover")}, clock=lambda: later)
    claimed_after_lease = await after.run_once()

    assert (held["status"], held["attempts"]) == ("pending", 1)
    assert during == 0  # not claimable while the lease holds
    assert claimed_after_lease == 1
    assert await rows(seeded.url, "SELECT job, runner FROM effects") == [
        {"job": 1, "runner": "takeover"}
    ]
    done = await job(seeded.url, job_id)
    assert (done["status"], done["attempts"]) == ("done", 2)  # the lost try is counted


async def test_a_handler_that_hangs_is_cut_off_at_the_lease_so_it_cannot_block_the_queue(
    engine: AsyncEngine, seeded: SeededDatabase
) -> None:
    hung, fine = await enqueue(seeded.url, "test.hang"), await enqueue(seeded.url, "test.effect")

    async def hang(tx: CommandTx, payload: dict[str, Any]) -> None:
        await asyncio.Event().wait()

    runner = Runner(
        engine, {"test.hang": hang, "test.effect": effect("after")}, lease_seconds=0.3, batch=10
    )
    await runner.run_once()

    stuck = await job(seeded.url, hung[0])
    assert (stuck["status"], stuck["attempts"]) == ("pending", 1)
    assert "TimeoutError" in stuck["last_error"]
    assert (await job(seeded.url, fine[0]))["status"] == "done"  # the job behind it still ran


# ----- retries and dead letters -----------------------------------------------------------------


async def test_a_job_that_always_fails_backs_off_dies_after_8_tries_and_can_be_requeued(
    engine: AsyncEngine, seeded: SeededDatabase, api: httpx.AsyncClient
) -> None:
    (job_id,) = await enqueue(seeded.url, "test.boom")
    now = datetime.now(UTC)
    tick = {"k": 0}

    async def boom(tx: CommandTx, payload: dict[str, Any]) -> None:
        raise RuntimeError("downstream is down")

    def clock() -> datetime:
        return now + timedelta(seconds=1000 * tick["k"])  # past every backoff so far

    runner = Runner(engine, {"test.boom": boom}, clock=clock)
    for attempt in range(1, 9):
        tick["k"] = attempt
        assert await runner.run_once() == 1
        row = await job(seeded.url, job_id)
        wait = (row["available_at"] - clock()).total_seconds()
        assert row["attempts"] == attempt
        if attempt < 8:
            assert row["status"] == "pending"
            assert (
                min(2**attempt, 300) <= wait <= min(2**attempt, 300) * 1.25 + 1
            )  # backoff + jitter
    assert row["status"] == "dead"
    assert "downstream is down" in row["last_error"]
    tick["k"] = 20
    assert await runner.run_once() == 0  # a dead job is never claimed again

    await signed_in(api, ADMIN)
    dead = (await api.get("/api/v1/admin/jobs", params={"status": "dead"})).json()["items"]
    retried = await api.post(f"/api/v1/admin/jobs/{job_id}/retry", headers=csrf_headers(api))
    again = await api.post(f"/api/v1/admin/jobs/{job_id}/retry", headers=csrf_headers(api))

    assert [j["id"] for j in dead] == [job_id]
    assert dead[0]["attempts"] == 8
    assert retried.status_code == 200
    assert (retried.json()["status"], retried.json()["attempts"]) == ("pending", 0)
    assert again.status_code == 409  # only a dead job can be retried
    assert await signed_in_non_admin_status(api) == 403


async def signed_in_non_admin_status(api: httpx.AsyncClient) -> int:
    await signed_in(api, MEERA)
    return (await api.get("/api/v1/admin/jobs")).status_code


# ----- notifications ----------------------------------------------------------------------------


async def test_the_same_job_delivered_twice_notifies_each_person_once_and_never_the_actor(
    engine: AsyncEngine, seeded: SeededDatabase, api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)  # the requester, and so a watcher
    item = await created(api, title="Worker fan-out check")
    async with signed_in_as(app_settings, ASHA) as asha:
        await claimed(asha, item)  # the actor of the assigned event
    runner = Runner(engine)
    await drain(runner)
    before = await rows(
        seeded.url, "SELECT user_id, event_id FROM notifications n ORDER BY event_id, user_id"
    )
    await execute(  # the broker delivers every job a second time
        seeded.url,
        "INSERT INTO outbox (topic, payload, status) "
        "SELECT topic, payload, 'pending' FROM outbox WHERE topic = 'item.event'",
    )

    await drain(runner)

    after = await rows(seeded.url, "SELECT user_id, event_id FROM notifications")
    duplicates = await rows(
        seeded.url,
        "SELECT user_id, event_id FROM notifications "
        "GROUP BY user_id, event_id HAVING count(*) > 1",
    )
    meera = await scalar(seeded.url, "SELECT id FROM users WHERE email = $1", MEERA)
    asha_id = await scalar(seeded.url, "SELECT id FROM users WHERE email = $1", ASHA)
    mine = await rows(
        seeded.url,
        "SELECT n.user_id, e.kind FROM notifications n JOIN item_events e ON e.id = n.event_id "
        "JOIN work_items w ON w.id = n.item_id WHERE w.key = $1",
        item["key"],
    )
    assert duplicates == []
    assert len(after) == len(before)  # the second delivery added nothing
    assert (meera, "assigned") in [(m["user_id"], m["kind"]) for m in mine]
    assert asha_id not in [m["user_id"] for m in mine if m["kind"] == "assigned"]  # not the actor
    assert meera not in [m["user_id"] for m in mine if m["kind"] == "created"]  # nor Meera's own


async def test_a_watcher_who_cannot_view_a_confidential_item_is_not_notified(
    engine: AsyncEngine, seeded: SeededDatabase, api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, team_key="CMP", type="compliance_request", title="Hidden fan-out")
    await execute(  # Farah watches it, but a plain CMP member cannot see a confidential item
        seeded.url,
        "INSERT INTO watchers (item_id, user_id) SELECT $1, id FROM users WHERE email = $2",
        uuid.UUID(item["id"]),
        FARAH,
    )
    async with signed_in_as(app_settings, ISHAAN) as lead:
        await ok(
            await act(
                lead, item["key"], "comments", {"body": "Lead note"}, idempotency_key=new_key()
            ),
            201,
        )

    await drain(Runner(engine))

    who = await rows(
        seeded.url,
        "SELECT u.email FROM notifications n JOIN users u ON u.id = n.user_id "
        "JOIN item_events e ON e.id = n.event_id WHERE n.item_id = $1 AND e.kind = 'commented'",
        uuid.UUID(item["id"]),
    )
    assert [w["email"] for w in who] == [MEERA]  # the requester; not Farah, not the actor Ishaan


# ----- duplicates -------------------------------------------------------------------------------


async def test_a_similar_open_item_is_suggested_once_even_if_the_job_runs_twice(
    engine: AsyncEngine, seeded: SeededDatabase, api: httpx.AsyncClient
) -> None:
    await signed_in(api, MEERA)
    first = await created(api, title="Settlement file rejected by the bank gateway")
    second = await created(api, title="Settlement file rejected by the bank gateway again")
    runner = Runner(engine)
    await drain(runner)
    await execute(
        seeded.url,
        "INSERT INTO outbox (topic, payload) SELECT topic, payload FROM outbox "
        "WHERE topic = 'item.created'",
    )  # a redelivery (the dedupe key only guards the enqueue)

    await drain(runner)

    suggested = await rows(
        seeded.url,
        "SELECT count(*) AS n FROM item_events e JOIN work_items w ON w.id = e.item_id "
        "WHERE w.key = $1 AND e.kind = 'duplicate_suggested'",
        second["key"],
    )
    similar = await rows(
        seeded.url,
        "SELECT o.key FROM item_similar s JOIN work_items w ON w.id = s.item_id "
        "JOIN work_items o ON o.id = s.similar_item_id WHERE w.key = $1",
        second["key"],
    )
    assert suggested == [{"n": 1}]
    assert [s["key"] for s in similar] == [first["key"]]


# ----- SLA sweep --------------------------------------------------------------------------------


async def test_two_sla_sweeps_at_once_write_one_sla_breached_event_per_overdue_item(
    engine: AsyncEngine, seeded: SeededDatabase, api: httpx.AsyncClient
) -> None:
    await signed_in(api, MEERA)
    late = [await created(api, title=f"Overdue number {n}") for n in range(3)]
    on_time = await created(api, title="Still has time")
    keys = [i["key"] for i in late]
    await execute(
        seeded.url,
        "UPDATE work_items SET due_at = now() - interval '1 hour' WHERE key = ANY($1)",
        keys,
    )

    marked = await asyncio.gather(sla_sweep(engine), sla_sweep(engine))
    again = await sla_sweep(engine)

    per_item = await rows(
        seeded.url,
        "SELECT w.key, count(*) AS n FROM item_events e JOIN work_items w ON w.id = e.item_id "
        "WHERE e.kind = 'sla_breached' GROUP BY w.key",
    )
    by_key = {r["key"]: r["n"] for r in per_item}
    assert all(by_key[k] == 1 for k in keys)
    assert set(by_key.values()) == {1}  # no item anywhere has two
    assert on_time["key"] not in by_key
    assert sum(marked) >= 3
    assert again == 0  # the second pass finds nothing left
    version = await scalar(seeded.url, "SELECT version FROM work_items WHERE key = $1", keys[0])
    assert version == late[0]["version"]  # an SLA breach never bumps the version (SPEC 6.2)
