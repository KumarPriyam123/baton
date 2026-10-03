"""SPEC 3.2: the database rejects impossible states, whatever the application does."""

import json

import asyncpg
import pytest

from .factories import World, insert, make_event, make_item, make_user
from .violations import VIOLATIONS, Violation


def _ids(violations: list[Violation]) -> list[str]:
    return [v.id for v in violations]


@pytest.mark.parametrize("violation", VIOLATIONS, ids=_ids(VIOLATIONS))
async def test_database_rejects_it_and_names_the_guard(
    conn: asyncpg.Connection, world: World, violation: Violation
) -> None:
    with pytest.raises(violation.error) as excinfo:
        async with conn.transaction():  # savepoint, so the connection stays usable
            await violation.run(conn, world)

    error = excinfo.value
    if violation.in_message is None:
        assert error.constraint_name == violation.guard
    else:
        assert violation.in_message in str(error)


@pytest.mark.parametrize("violation", VIOLATIONS, ids=_ids(VIOLATIONS))
async def test_it_only_fails_because_of_that_guard(
    conn: asyncpg.Connection, world: World, violation: Violation
) -> None:
    """Sabotage check: drop the guard and the same statement must go through.

    If it still failed, some other rule would be rejecting it and the test above would prove
    nothing about this guard. DDL is transactional in Postgres, so the drop is rolled back.
    """
    await conn.execute(violation.drop)

    await violation.run(conn, world)  # must not raise


# Positive controls: the same shapes, made valid, are accepted (a constraint that is too strict
# would break the application just as surely as one that is missing).


@pytest.mark.parametrize("status", ["in_progress", "blocked", "awaiting_approval"])
async def test_every_active_status_requires_an_owner(
    conn: asyncpg.Connection, world: World, status: str
) -> None:
    with pytest.raises(asyncpg.CheckViolationError) as excinfo:
        async with conn.transaction():
            await make_item(conn, world.team, world.requester, status=status)
    assert excinfo.value.constraint_name == "owner_when_active"

    row = await make_item(
        conn, world.team, world.requester, status=status, assignee_id=world.member
    )
    assert row["status"] == status


@pytest.mark.parametrize("status", ["new", "resolved", "closed"])
async def test_other_statuses_do_not_need_an_owner(
    conn: asyncpg.Connection, world: World, status: str
) -> None:
    extra = {"resolution": "done"} if status in ("resolved", "closed") else {}
    row = await make_item(conn, world.team, world.requester, status=status, **extra)
    assert row["assignee_id"] is None


@pytest.mark.parametrize("status", ["resolved", "closed"])
async def test_finished_items_need_a_resolution(
    conn: asyncpg.Connection, world: World, status: str
) -> None:
    with pytest.raises(asyncpg.CheckViolationError) as excinfo:
        async with conn.transaction():
            await make_item(conn, world.team, world.requester, status=status)
    assert excinfo.value.constraint_name == "resolution_when_done"


async def test_duplicate_resolution_with_a_target_is_accepted(
    conn: asyncpg.Connection, world: World
) -> None:
    row = await make_item(
        conn,
        world.team,
        world.requester,
        status="closed",
        resolution="duplicate",
        duplicate_of_id=world.item_id,
    )
    assert row["duplicate_of_id"] == world.item_id


async def test_two_approvals_are_fine_when_only_one_is_pending(
    conn: asyncpg.Connection, world: World
) -> None:
    await insert(
        conn,
        "approvals",
        item_id=world.item_id,
        status="invalidated",
        requested_by=world.member,
        subject_hash="old",
    )
    await insert(
        conn, "approvals", item_id=world.item_id, requested_by=world.member, subject_hash="new"
    )
    other = await make_item(conn, world.team, world.requester)
    await insert(
        conn, "approvals", item_id=other["id"], requested_by=world.member, subject_hash="x"
    )


async def test_a_lead_other_than_the_requester_can_approve(
    conn: asyncpg.Connection, world: World
) -> None:
    row = await insert(
        conn,
        "approvals",
        item_id=world.item_id,
        status="approved",
        requested_by=world.member,
        decided_by=world.lead,
        subject_hash="abc",
    )
    assert row["decided_by"] == world.lead


async def test_a_rejection_with_a_note_is_accepted(conn: asyncpg.Connection, world: World) -> None:
    row = await insert(
        conn,
        "approvals",
        item_id=world.item_id,
        status="rejected",
        requested_by=world.member,
        decided_by=world.lead,
        decision_note="Amount does not match the ledger.",
        subject_hash="abc",
    )
    assert row["status"] == "rejected"


async def test_outbox_rows_without_a_dedupe_key_never_collide(
    conn: asyncpg.Connection, world: World
) -> None:
    await insert(conn, "outbox", topic="item.event")
    await insert(conn, "outbox", topic="item.event")


# Identities never change, but everything around them still can


async def test_a_transfer_changes_team_but_not_the_key(
    conn: asyncpg.Connection, world: World
) -> None:
    await conn.execute(
        "UPDATE work_items SET team_id = $2, version = version + 1 WHERE id = $1",
        world.item_id,
        world.other_team.id,
    )

    row = await conn.fetchrow(
        "SELECT key, team_id, origin_team_id FROM work_items WHERE id = $1", world.item_id
    )
    assert row is not None
    assert row["key"] == world.item_key
    assert row["team_id"] == world.other_team.id
    assert row["origin_team_id"] == world.team.id


async def test_the_team_counter_and_name_can_still_change(
    conn: asyncpg.Connection, world: World
) -> None:
    await conn.execute(
        "UPDATE teams SET item_seq = item_seq + 1, name = 'Payments' WHERE id = $1", world.team.id
    )

    name = await conn.fetchval("SELECT name FROM teams WHERE id = $1", world.team.id)
    assert name == "Payments"


async def test_writing_the_same_key_back_is_not_a_change(
    conn: asyncpg.Connection, world: World
) -> None:
    result = await conn.execute(
        "UPDATE work_items SET key = $2, number = $3 WHERE id = $1",
        world.item_id,
        world.item_key,
        world.item_number,
    )

    assert result == "UPDATE 1"


# Append-only history


async def test_events_can_still_be_inserted(conn: asyncpg.Connection, world: World) -> None:
    row = await make_event(conn, world.item_id, world.team.id, kind="commented", item_version=2)
    assert row["id"] > world.event_id


async def test_an_update_that_matches_no_event_is_harmless(
    conn: asyncpg.Connection, world: World
) -> None:
    result = await conn.execute("UPDATE item_events SET reason = 'x' WHERE id = -1")
    assert result == "UPDATE 0"


async def test_the_refused_update_leaves_the_event_unchanged(
    conn: asyncpg.Connection, world: World
) -> None:
    with pytest.raises(asyncpg.RestrictViolationError):
        async with conn.transaction():
            await conn.execute(
                "UPDATE item_events SET reason = 'rewritten' WHERE id = $1", world.event_id
            )

    reason = await conn.fetchval("SELECT reason FROM item_events WHERE id = $1", world.event_id)
    assert reason is None


async def test_event_ids_are_generated_never_supplied(
    conn: asyncpg.Connection, world: World
) -> None:
    with pytest.raises(asyncpg.GeneratedAlwaysError):
        async with conn.transaction():
            await insert(
                conn,
                "item_events",
                id=999_999,
                item_id=world.item_id,
                team_id=world.team.id,
                kind="created",
                item_version=1,
                data=json.dumps({}),
            )


# Case-insensitive email


async def test_email_lookup_ignores_case(conn: asyncpg.Connection, world: World) -> None:
    user_id = await make_user(conn, email="Asha.Rao@Baton.test")
    found = await conn.fetchval("SELECT id FROM users WHERE email = 'asha.rao@baton.test'")
    assert found == user_id


# Generated search column (SPEC 8)


async def test_search_column_is_generated_and_cannot_be_written(
    conn: asyncpg.Connection, world: World
) -> None:
    with pytest.raises(asyncpg.GeneratedAlwaysError):
        async with conn.transaction():
            await conn.execute(
                "UPDATE work_items SET search = to_tsvector('x') WHERE id = $1", world.item_id
            )


async def test_search_matches_title_description_and_key(
    conn: asyncpg.Connection, world: World
) -> None:
    row = await make_item(
        conn,
        world.team,
        world.requester,
        title="Refund stuck for order 48213",
        description="Customer was charged twice by the acquirer.",
    )
    sql = "SELECT count(*) FROM work_items WHERE id = $1 AND search @@ {query}"
    stemmed_title = sql.format(query="websearch_to_tsquery('english', 'refunds')")
    stemmed_description = sql.format(query="websearch_to_tsquery('english', 'charge')")
    by_key = sql.format(query=f"websearch_to_tsquery('simple', '{row['key'].lower()}')")

    assert await conn.fetchval(stemmed_title, row["id"]) == 1
    assert await conn.fetchval(stemmed_description, row["id"]) == 1
    assert await conn.fetchval(by_key, row["id"]) == 1


async def test_search_follows_updates_to_the_title(conn: asyncpg.Connection, world: World) -> None:
    await conn.execute(
        "UPDATE work_items SET title = 'Chargeback dispute' WHERE id = $1", world.item_id
    )

    hit = await conn.fetchval(
        "SELECT count(*) FROM work_items WHERE id = $1 "
        "AND search @@ websearch_to_tsquery('english', 'chargeback')",
        world.item_id,
    )
    assert hit == 1
