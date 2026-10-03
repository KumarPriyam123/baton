"""The demo seed, loaded into a real database: idempotent, resettable, valid and demonstrable."""

import asyncio
import re
import time
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

import asyncpg
import pytest
from alembic import command
from argon2 import PasswordHasher

from app.db.urls import asyncpg_dsn, with_database
from scripts.seedlib import generate, load
from scripts.seedlib.personas import DEMO_PASSWORD
from scripts.seedlib.verify import verify_histories
from tests.support.db import alembic_config

LOAD_BUDGET_SECONDS = 30  # BUILD_PLAN phase 1: demo seed loads in under 30 s


class SeededDatabase:
    def __init__(self, url: str, dataset: generate.Dataset, seconds: float) -> None:
        self.url = url
        self.dataset = dataset
        self.seconds = seconds

    async def connect(self) -> asyncpg.Connection:
        return await asyncpg.connect(asyncpg_dsn(self.url))


async def _admin(test_url: str) -> asyncpg.Connection:
    return await asyncpg.connect(asyncpg_dsn(with_database(test_url, "postgres")))


async def _create(test_url: str, name: str) -> None:
    admin = await _admin(test_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()


async def _drop(test_url: str, name: str) -> None:
    admin = await _admin(test_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _load(url: str, dataset: generate.Dataset, *, reset: bool = False) -> bool:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return await load.load(conn, dataset, reset=reset)
    finally:
        await conn.close()


def _build() -> generate.Dataset:
    return generate.build_dataset(
        "demo",
        seed=42,
        now=datetime.now(UTC),
        password_hash=PasswordHasher().hash(DEMO_PASSWORD),
    )


@pytest.fixture(scope="module")
def seeded(test_database_url: str) -> Iterator[SeededDatabase]:
    name = f"baton_seed_{uuid.uuid4().hex[:8]}"
    asyncio.run(_create(test_database_url, name))
    url = with_database(test_database_url, name)
    try:
        command.upgrade(alembic_config(url), "head")
        started = time.perf_counter()
        dataset = _build()
        assert asyncio.run(_load(url, dataset)) is True
        yield SeededDatabase(url, dataset, time.perf_counter() - started)
    finally:
        asyncio.run(_drop(test_database_url, name))


async def _rows(seeded: SeededDatabase, sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await seeded.connect()
    try:
        rows: list[asyncpg.Record] = await conn.fetch(sql, *args)
    finally:
        await conn.close()
    return rows


async def _value(seeded: SeededDatabase, sql: str, *args: Any) -> Any:
    rows = await _rows(seeded, sql, *args)
    return rows[0][0]


# ----- loading ----------------------------------------------------------------------------


def test_demo_seed_generates_and_loads_within_the_budget(seeded: SeededDatabase) -> None:
    assert seeded.seconds < LOAD_BUDGET_SECONDS


async def test_the_database_holds_exactly_what_was_generated(seeded: SeededDatabase) -> None:
    expected = seeded.dataset.counts()
    tables = {
        "users": "users",
        "teams": "teams",
        "memberships": "memberships",
        "items": "work_items",
        "events": "item_events",
        "comments": "comments",
        "approvals": "approvals",
        "watchers": "watchers",
        "item_reads": "item_reads",
        "item_similar": "item_similar",
    }

    for key, table in tables.items():
        assert await _value(seeded, f"SELECT count(*) FROM {table}") == expected[key], table  # noqa: S608


async def test_sizes_match_the_spec_for_the_demo_seed(seeded: SeededDatabase) -> None:
    counts = seeded.dataset.counts()

    assert counts["users"] == 24
    assert counts["teams"] == 6
    assert counts["items"] == 600
    assert 4_000 <= counts["events"] <= 6_000  # SPEC 13.1: ~5,000
    assert 900 <= counts["comments"] <= 1_500  # SPEC 13.1: ~1,200


# ----- histories --------------------------------------------------------------------------


async def test_every_seeded_history_replays_to_its_stored_state(seeded: SeededDatabase) -> None:
    conn = await seeded.connect()
    try:
        checked, problems = await verify_histories(conn)
    finally:
        await conn.close()

    assert checked == 600
    assert problems == []


async def test_twenty_random_items_have_valid_histories(seeded: SeededDatabase) -> None:
    """The BUILD_PLAN spot check, run for several different samples."""
    conn = await seeded.connect()
    try:
        for sample_seed in range(5):
            checked, problems = await verify_histories(conn, sample=20, seed=sample_seed)
            assert checked == 20
            assert problems == []
    finally:
        await conn.close()


async def test_comments_and_system_events_never_change_the_version(
    seeded: SeededDatabase,
) -> None:
    changed = await _value(
        seeded,
        "SELECT count(*) FROM (SELECT kind, item_version, "
        "lag(item_version) OVER (PARTITION BY item_id ORDER BY id) AS before FROM item_events) e "
        "WHERE kind IN ('commented', 'sla_breached', 'duplicate_suggested') "
        "AND item_version IS DISTINCT FROM before",
    )
    comments = await _value(seeded, "SELECT count(*) FROM item_events WHERE kind = 'commented'")

    assert comments > 0
    assert changed == 0


async def test_read_markers_point_at_events_of_their_own_item(seeded: SeededDatabase) -> None:
    stray = await _value(
        seeded,
        "SELECT count(*) FROM item_reads r WHERE NOT EXISTS "
        "(SELECT 1 FROM item_events e WHERE e.id = r.last_read_event_id AND e.item_id = r.item_id)",
    )
    total = await _value(seeded, "SELECT count(*) FROM item_reads")
    unread = await _value(
        seeded,
        "SELECT count(*) FROM item_reads r JOIN work_items i ON i.id = r.item_id "
        "WHERE r.last_read_event_id < i.last_event_id",
    )

    assert total > 300
    assert stray == 0
    assert unread > 50  # "Your requests" has activity since the reader last looked


async def test_event_ids_follow_time(seeded: SeededDatabase) -> None:
    out_of_order = await _value(
        seeded,
        "SELECT count(*) FROM (SELECT created_at, lag(created_at) OVER (ORDER BY id) AS prev "
        "FROM item_events) e WHERE prev > created_at",
    )

    assert out_of_order == 0


async def test_keys_follow_the_origin_team_and_counters_match(seeded: SeededDatabase) -> None:
    bad_keys = await _value(
        seeded,
        "SELECT count(*) FROM work_items i JOIN teams t ON t.id = i.origin_team_id "
        "WHERE i.key <> t.key || '-' || i.number",
    )
    wrong_counters = await _value(
        seeded,
        "SELECT count(*) FROM teams t WHERE t.item_seq <> "
        "(SELECT coalesce(max(number), 0) FROM work_items i WHERE i.origin_team_id = t.id)",
    )

    assert bad_keys == 0
    assert wrong_counters == 0


async def test_numbers_per_team_are_contiguous(seeded: SeededDatabase) -> None:
    gaps = await _value(
        seeded,
        "SELECT count(*) FROM (SELECT origin_team_id, count(*) AS n, max(number) AS top "
        "FROM work_items GROUP BY 1) t WHERE n <> top",
    )

    assert gaps == 0


async def test_last_event_ids_point_at_each_items_last_event(seeded: SeededDatabase) -> None:
    wrong = await _value(
        seeded,
        "SELECT count(*) FROM work_items i WHERE i.last_event_id IS DISTINCT FROM "
        "(SELECT max(id) FROM item_events e WHERE e.item_id = i.id)",
    )

    assert wrong == 0


async def test_everyone_shares_the_demo_password(seeded: SeededDatabase) -> None:
    hashes = await _rows(seeded, "SELECT DISTINCT password_hash FROM users")

    assert len(hashes) == 1
    assert PasswordHasher().verify(hashes[0]["password_hash"], DEMO_PASSWORD)


# ----- the demo has something to show for every role --------------------------------------

PRIYA, ASHA, MEERA, FARAH, DEV, SAMEER = (
    f"{name}@baton.test"
    for name in ("priya.lead", "asha", "meera", "farah", "dev.viewer", "sameer")
)


async def test_named_personas_hold_the_roles_the_spec_gives_them(seeded: SeededDatabase) -> None:
    rows = await _rows(
        seeded,
        "SELECT u.email::text, t.key, m.role::text FROM memberships m "
        "JOIN users u ON u.id = m.user_id JOIN teams t ON t.id = m.team_id",
    )
    roles = {(r[0], r[1]): r[2] for r in rows}

    assert roles[(PRIYA, "PAY")] == "lead"
    assert roles[(ASHA, "PAY")] == "member"
    assert roles[("rahul@baton.test", "PAY")] == "member"
    assert roles[(MEERA, "SUP")] == "member"
    assert roles[(DEV, "PAY")] == "viewer"
    assert roles[(DEV, "SRE")] == "viewer"
    admin = await _value(seeded, "SELECT is_admin FROM users WHERE email = 'admin@baton.test'")
    assert admin is True


async def test_every_team_has_two_leads_so_four_eyes_is_possible(seeded: SeededDatabase) -> None:
    thin = await _rows(
        seeded,
        "SELECT t.key FROM teams t WHERE (SELECT count(*) FROM memberships m "
        "WHERE m.team_id = t.id AND m.role = 'lead') < 2",
    )

    assert thin == []


async def test_a_lead_has_approvals_waiting_that_they_did_not_request(
    seeded: SeededDatabase,
) -> None:
    waiting = await _value(
        seeded,
        "SELECT count(*) FROM approvals a JOIN work_items i ON i.id = a.item_id "
        "JOIN users u ON u.email = $1 "
        "JOIN memberships m ON m.user_id = u.id AND m.team_id = i.team_id "
        "WHERE a.status = 'pending' AND a.requested_by <> u.id AND m.role = 'lead'",
        PRIYA,
    )
    teams_with_pending = await _value(
        seeded,
        "SELECT count(DISTINCT i.team_id) FROM approvals a JOIN work_items i ON i.id = a.item_id "
        "WHERE a.status = 'pending'",
    )

    assert waiting >= 1
    assert teams_with_pending >= 4


async def test_compliance_items_are_confidential_and_hidden_from_other_members(
    seeded: SeededDatabase,
) -> None:
    # Compliance requests are confidential at creation (SPEC A12); a lead may switch it off later.
    not_confidential_at_creation = await _value(
        seeded,
        "SELECT count(*) FROM work_items i JOIN item_events e ON e.item_id = i.id "
        "AND e.kind = 'created' WHERE i.type = 'compliance_request' "
        "AND e.data -> 'confidential' ->> 'to' <> 'true'",
    )
    still_confidential = await _value(
        seeded,
        "SELECT avg((confidential)::int) FROM work_items WHERE type = 'compliance_request'",
    )
    hidden_from_farah = await _value(
        seeded,
        "SELECT count(*) FROM work_items i JOIN teams t ON t.id = i.team_id, users f "
        "WHERE f.email = $1 AND t.key = 'CMP' AND i.confidential "
        "AND i.assignee_id IS DISTINCT FROM f.id AND i.requester_id <> f.id",
        FARAH,
    )
    farah_owns = await _value(
        seeded,
        "SELECT count(*) FROM work_items i JOIN users f ON f.id = i.assignee_id "
        "WHERE f.email = $1 AND i.confidential",
        FARAH,
    )

    assert not_confidential_at_creation == 0
    assert still_confidential > 0.7
    assert hidden_from_farah >= 5  # she is a CMP member but cannot see these
    assert farah_owns >= 1  # and can see the ones she works


async def test_the_demo_has_overdue_unowned_and_going_quiet_work(seeded: SeededDatabase) -> None:
    overdue = await _value(
        seeded,
        "SELECT count(*) FROM work_items WHERE status NOT IN ('resolved', 'closed') "
        "AND sla_breached_at IS NOT NULL",
    )
    unowned = await _value(
        seeded, "SELECT count(*) FROM work_items WHERE status = 'new' AND assignee_id IS NULL"
    )
    quiet = await _value(
        seeded,
        "SELECT count(*) FROM work_items WHERE status IN ('in_progress', 'blocked') "
        "AND last_activity_at < now() - interval '72 hours'",
    )

    assert overdue >= 10
    assert unowned >= 10
    assert quiet >= 3


async def test_people_have_their_own_work_to_open(seeded: SeededDatabase) -> None:
    asha_open = await _value(
        seeded,
        "SELECT count(*) FROM work_items i JOIN users u ON u.id = i.assignee_id "
        "WHERE u.email = $1 AND i.status IN ('in_progress', 'blocked', 'awaiting_approval')",
        ASHA,
    )
    meera_requests = await _value(
        seeded,
        "SELECT count(*) FROM work_items i JOIN users u ON u.id = i.requester_id "
        "WHERE u.email = $1",
        MEERA,
    )
    dev_watching = await _value(
        seeded,
        "SELECT count(*) FROM watchers w JOIN users u ON u.id = w.user_id WHERE u.email = $1",
        DEV,
    )

    assert asha_open >= 3
    assert meera_requests >= 20
    assert dev_watching >= 1


async def test_both_resolutions_and_reopened_items_exist(seeded: SeededDatabase) -> None:
    resolutions = {
        r[0]
        for r in await _rows(
            seeded, "SELECT DISTINCT resolution::text FROM work_items WHERE resolution IS NOT NULL"
        )
    }
    reopened = await _value(
        seeded, "SELECT count(DISTINCT item_id) FROM item_events WHERE kind = 'reopened'"
    )

    assert {"done", "duplicate", "wont_do", "cannot_reproduce"} <= resolutions
    assert reopened >= 5


async def test_unread_notifications_exist_for_the_demo_users(seeded: SeededDatabase) -> None:
    unread = await _value(seeded, "SELECT count(*) FROM notifications WHERE read_at IS NULL")

    assert unread >= 50


async def test_seeded_titles_are_realistic_and_keys_look_like_keys(seeded: SeededDatabase) -> None:
    rows = await _rows(seeded, "SELECT key, title FROM work_items LIMIT 200")

    assert all(re.fullmatch(r"[A-Z]{2,5}-\d+", r["key"]) for r in rows)
    assert all("{" not in r["title"] for r in rows)  # no unfilled template placeholders


# ----- idempotence and reset --------------------------------------------------------------


async def test_loading_again_without_reset_changes_nothing(seeded: SeededDatabase) -> None:
    before = await _value(seeded, "SELECT count(*) FROM item_events")

    loaded = await _load(seeded.url, _build(), reset=False)

    assert loaded is False
    assert await _value(seeded, "SELECT count(*) FROM item_events") == before


async def test_reset_wipes_and_reloads_without_leaving_stragglers(
    seeded: SeededDatabase, test_database_url: str
) -> None:
    conn = await seeded.connect()
    try:
        await conn.execute("INSERT INTO outbox (topic) VALUES ('leftover')")
    finally:
        await conn.close()

    assert await _load(seeded.url, _build(), reset=True) is True

    assert await _value(seeded, "SELECT count(*) FROM outbox") == 0
    assert await _value(seeded, "SELECT count(*) FROM work_items") == 600
    assert await _value(seeded, "SELECT min(id) FROM item_events") == 1  # identity restarted
    conn = await seeded.connect()
    try:
        _, problems = await verify_histories(conn)
    finally:
        await conn.close()
    assert problems == []
