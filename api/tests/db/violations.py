"""Every database-enforced rule in SPEC 3.2, as a statement that breaks it.

Each Violation names the guard (constraint, unique index or trigger) it targets and the SQL that
removes that guard. test_constraints.py uses the registry twice:

1. the statement must be rejected, and the error must name this guard;
2. with the guard dropped (inside the rolled-back test transaction) the same statement must
   succeed. If it still fails, something else was rejecting it and the test proved nothing.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import asyncpg

from .factories import World, insert, make_event, make_item, make_user

Run = Callable[[asyncpg.Connection, World], Awaitable[object]]


@dataclass(frozen=True)
class Violation:
    id: str
    guard: str  # name shown in the database error
    drop: str  # SQL that removes the guard
    run: Run
    error: type[asyncpg.PostgresError]
    in_message: str | None = None  # for the trigger, which has no constraint name


def _drop_constraint(table: str, name: str) -> str:
    return f"ALTER TABLE {table} DROP CONSTRAINT {name}"


async def _owner_when_active(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, status="in_progress")


async def _resolution_when_done(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, status="resolved")


async def _duplicate_has_target(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, status="closed", resolution="duplicate")


async def _not_self_duplicate(conn: asyncpg.Connection, w: World) -> object:
    item_id = uuid.uuid4()
    return await make_item(
        conn,
        w.team,
        w.requester,
        id=item_id,
        status="closed",
        resolution="duplicate",
        duplicate_of_id=item_id,
    )


async def _duplicate_key(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, key=w.item_key)


async def _duplicate_origin_number(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, key="PAY-OTHER", number=w.item_number)


async def _title_too_short(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, title="ab")


async def _priority_out_of_range(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, priority=4)


async def _description_too_long(conn: asyncpg.Connection, w: World) -> object:
    return await make_item(conn, w.team, w.requester, description="x" * 20_001)


async def _team_key_lowercase(conn: asyncpg.Connection, w: World) -> object:
    return await insert(conn, "teams", key="pay", name="Lowercase")


async def _team_key_duplicate(conn: asyncpg.Connection, w: World) -> object:
    return await insert(conn, "teams", key=w.team.key, name="Again")


async def _email_differs_only_by_case(conn: asyncpg.Connection, w: World) -> object:
    await make_user(conn, email="Dup.Case@Baton.test")
    return await make_user(conn, email="dup.case@baton.test")


async def _empty_comment(conn: asyncpg.Connection, w: World) -> object:
    return await insert(conn, "comments", item_id=w.item_id, author_id=w.member, body="")


async def _unknown_event_kind(conn: asyncpg.Connection, w: World) -> object:
    return await make_event(conn, w.item_id, w.team.id, kind="teleported")


async def _two_pending_approvals(conn: asyncpg.Connection, w: World) -> object:
    for _ in range(2):
        await insert(
            conn, "approvals", item_id=w.item_id, requested_by=w.member, subject_hash="abc"
        )
    return None


async def _approver_is_requester(conn: asyncpg.Connection, w: World) -> object:
    return await insert(
        conn,
        "approvals",
        item_id=w.item_id,
        status="approved",
        requested_by=w.lead,
        decided_by=w.lead,
        subject_hash="abc",
    )


async def _rejection_without_note(conn: asyncpg.Connection, w: World) -> object:
    return await insert(
        conn,
        "approvals",
        item_id=w.item_id,
        status="rejected",
        requested_by=w.member,
        decided_by=w.lead,
        decision_note="   ",
        subject_hash="abc",
    )


async def _same_notification_twice(conn: asyncpg.Connection, w: World) -> object:
    for _ in range(2):
        await insert(
            conn,
            "notifications",
            user_id=w.member,
            item_id=w.item_id,
            event_id=w.event_id,
            kind="assigned",
        )
    return None


async def _same_dedupe_key_twice(conn: asyncpg.Connection, w: World) -> object:
    for _ in range(2):
        await insert(conn, "outbox", topic="item.created", dedupe_key="dup:1")
    return None


async def _same_idempotency_key_twice(conn: asyncpg.Connection, w: World) -> object:
    for _ in range(2):
        await insert(conn, "idempotency_keys", user_id=w.member, key="k1", fingerprint="f")
    return None


async def _same_membership_twice(conn: asyncpg.Connection, w: World) -> object:
    return await insert(conn, "memberships", team_id=w.team.id, user_id=w.member, role="viewer")


async def _rename_team_key(conn: asyncpg.Connection, w: World) -> object:
    return await conn.execute("UPDATE teams SET key = 'ZZZ' WHERE id = $1", w.team.id)


async def _rekey_item(conn: asyncpg.Connection, w: World) -> object:
    return await conn.execute("UPDATE work_items SET key = 'PAY-999' WHERE id = $1", w.item_id)


async def _renumber_item(conn: asyncpg.Connection, w: World) -> object:
    return await conn.execute("UPDATE work_items SET number = 999 WHERE id = $1", w.item_id)


async def _change_origin_team(conn: asyncpg.Connection, w: World) -> object:
    return await conn.execute(
        "UPDATE work_items SET origin_team_id = $2 WHERE id = $1", w.item_id, w.other_team.id
    )


async def _update_event(conn: asyncpg.Connection, w: World) -> object:
    return await conn.execute(
        "UPDATE item_events SET reason = 'rewritten' WHERE id = $1", w.event_id
    )


async def _delete_event(conn: asyncpg.Connection, w: World) -> object:
    return await conn.execute("DELETE FROM item_events WHERE id = $1", w.event_id)


async def _update_event_to_same_values(conn: asyncpg.Connection, w: World) -> object:
    # Even a no-op rewrite is refused: the trigger is not "only if something changed".
    return await conn.execute("UPDATE item_events SET kind = kind WHERE id = $1", w.event_id)


CHECK = asyncpg.CheckViolationError
UNIQUE = asyncpg.UniqueViolationError
DROP_TRIGGER = "DROP TRIGGER item_events_append_only ON item_events"

VIOLATIONS: list[Violation] = [
    # work_items CHECK constraints
    Violation(
        "active_item_without_owner",
        "owner_when_active",
        _drop_constraint("work_items", "owner_when_active"),
        _owner_when_active,
        CHECK,
    ),
    Violation(
        "resolved_item_without_resolution",
        "resolution_when_done",
        _drop_constraint("work_items", "resolution_when_done"),
        _resolution_when_done,
        CHECK,
    ),
    Violation(
        "duplicate_without_target",
        "duplicate_has_target",
        _drop_constraint("work_items", "duplicate_has_target"),
        _duplicate_has_target,
        CHECK,
    ),
    Violation(
        "item_duplicate_of_itself",
        "not_self_duplicate",
        _drop_constraint("work_items", "not_self_duplicate"),
        _not_self_duplicate,
        CHECK,
    ),
    Violation(
        "title_shorter_than_3",
        "work_items_title_length",
        _drop_constraint("work_items", "work_items_title_length"),
        _title_too_short,
        CHECK,
    ),
    Violation(
        "priority_above_3",
        "work_items_priority_range",
        _drop_constraint("work_items", "work_items_priority_range"),
        _priority_out_of_range,
        CHECK,
    ),
    Violation(
        "description_over_20000",
        "work_items_description_length",
        _drop_constraint("work_items", "work_items_description_length"),
        _description_too_long,
        CHECK,
    ),
    # work_items uniqueness
    Violation(
        "second_item_with_same_key",
        "work_items_key_key",
        _drop_constraint("work_items", "work_items_key_key"),
        _duplicate_key,
        UNIQUE,
    ),
    Violation(
        "second_item_with_same_origin_number",
        "work_items_origin_number_key",
        _drop_constraint("work_items", "work_items_origin_number_key"),
        _duplicate_origin_number,
        UNIQUE,
    ),
    # teams, users, comments, events
    Violation(
        "team_key_not_uppercase",
        "teams_key_format",
        _drop_constraint("teams", "teams_key_format"),
        _team_key_lowercase,
        CHECK,
    ),
    Violation(
        "second_team_with_same_key",
        "teams_key_key",
        _drop_constraint("teams", "teams_key_key"),
        _team_key_duplicate,
        UNIQUE,
    ),
    Violation(
        "email_unique_ignoring_case",
        "users_email_key",
        _drop_constraint("users", "users_email_key"),
        _email_differs_only_by_case,
        UNIQUE,
    ),
    Violation(
        "empty_comment",
        "comments_body_length",
        _drop_constraint("comments", "comments_body_length"),
        _empty_comment,
        CHECK,
    ),
    Violation(
        "event_kind_not_in_list",
        "item_events_kind_valid",
        _drop_constraint("item_events", "item_events_kind_valid"),
        _unknown_event_kind,
        CHECK,
    ),
    # approvals
    Violation(
        "two_pending_approvals_for_one_item",
        "approvals_one_pending",
        "DROP INDEX approvals_one_pending",
        _two_pending_approvals,
        UNIQUE,
    ),
    Violation(
        "approver_is_the_requester",
        "approvals_four_eyes",
        _drop_constraint("approvals", "approvals_four_eyes"),
        _approver_is_requester,
        CHECK,
    ),
    Violation(
        "rejection_without_note",
        "approvals_rejection_has_note",
        _drop_constraint("approvals", "approvals_rejection_has_note"),
        _rejection_without_note,
        CHECK,
    ),
    # idempotency of fan-out and messaging
    Violation(
        "same_event_notified_twice",
        "notifications_user_event_key",
        _drop_constraint("notifications", "notifications_user_event_key"),
        _same_notification_twice,
        UNIQUE,
    ),
    Violation(
        "same_outbox_dedupe_key_twice",
        "outbox_dedupe_key_key",
        _drop_constraint("outbox", "outbox_dedupe_key_key"),
        _same_dedupe_key_twice,
        UNIQUE,
    ),
    Violation(
        "same_idempotency_key_twice",
        "idempotency_keys_pkey",
        _drop_constraint("idempotency_keys", "idempotency_keys_pkey"),
        _same_idempotency_key_twice,
        UNIQUE,
    ),
    Violation(
        "same_membership_twice",
        "memberships_pkey",
        _drop_constraint("memberships", "memberships_pkey"),
        _same_membership_twice,
        UNIQUE,
    ),
    # identities never change (trigger raises restrict_violation)
    Violation(
        "rename_a_team_key",
        "teams_key_immutable",
        "DROP TRIGGER teams_key_immutable ON teams",
        _rename_team_key,
        asyncpg.RestrictViolationError,
        in_message="immutable",
    ),
    Violation(
        "change_an_item_key",
        "work_items_identity_immutable",
        "DROP TRIGGER work_items_identity_immutable ON work_items",
        _rekey_item,
        asyncpg.RestrictViolationError,
        in_message="immutable",
    ),
    Violation(
        "change_an_item_number",
        "work_items_identity_immutable",
        "DROP TRIGGER work_items_identity_immutable ON work_items",
        _renumber_item,
        asyncpg.RestrictViolationError,
        in_message="immutable",
    ),
    Violation(
        "change_an_items_origin_team",
        "work_items_identity_immutable",
        "DROP TRIGGER work_items_identity_immutable ON work_items",
        _change_origin_team,
        asyncpg.RestrictViolationError,
        in_message="immutable",
    ),
    # append-only history (trigger raises restrict_violation)
    Violation(
        "update_an_event",
        "item_events_append_only",
        DROP_TRIGGER,
        _update_event,
        asyncpg.RestrictViolationError,
        in_message="append-only",
    ),
    Violation(
        "delete_an_event",
        "item_events_append_only",
        DROP_TRIGGER,
        _delete_event,
        asyncpg.RestrictViolationError,
        in_message="append-only",
    ),
    Violation(
        "rewrite_an_event_with_identical_values",
        "item_events_append_only",
        DROP_TRIGGER,
        _update_event_to_same_values,
        asyncpg.RestrictViolationError,
        in_message="append-only",
    ),
]
