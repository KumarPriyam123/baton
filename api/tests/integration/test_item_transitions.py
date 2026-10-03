"""Block, unblock, resolve, reopen, close, withdraw (SPEC 4.1) through the real app."""

import json
from typing import Any

import httpx

from app.config import Settings
from tests.integration.test_teams_members import signed_in
from tests.support.items import created, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.users import execute
from tests.support.workflow import (
    ASHA,
    MEERA,
    PRIYA,
    RAHUL,
    VIKRAM,
    act,
    claimed,
    decide,
    events,
    get,
    ok,
    requested_approval,
    row,
    transition,
)


async def new_item(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    await signed_in(api, MEERA)
    return await created(api, **overrides)


async def owned_by_asha(
    api: httpx.AsyncClient, app_settings: Settings, **overrides: Any
) -> dict[str, Any]:
    item = await new_item(api, **overrides)
    async with signed_in_as(app_settings, ASHA) as asha:
        return await claimed(asha, item)


# ----- block and unblock ------------------------------------------------------------------


async def test_the_owner_blocks_with_a_reason_and_unblocks(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        blocked = await ok(await transition(asha, item, "block", reason="Waiting on the bank"))
        view = await get(asha, item["key"])
        unblocked = await ok(await transition(asha, blocked, "unblock"))

    assert blocked["status"] == "blocked"
    assert view["next_step"]["label"] == "Blocked: Waiting on the bank"
    assert unblocked["status"] == "in_progress"
    kinds = [(e["kind"], e["item_version"]) for e in await events(seeded.url, item["key"])]
    assert kinds[-2:] == [("blocked", 3), ("unblocked", 4)]


async def test_blocking_needs_a_reason(api: httpx.AsyncClient, app_settings: Settings) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        blank = await transition(asha, item, "block", reason="   ")
        absent = await transition(asha, item, "block")

    for refused in (blank, absent):
        assert refused.status_code == 422
        assert refused.json()["code"] == "REASON_REQUIRED"


async def test_a_member_who_does_not_own_it_cannot_block_it(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, RAHUL) as rahul:
        refused = await transition(rahul, item, "block", reason="Not mine")

    assert refused.status_code == 403


async def test_blocking_a_new_item_is_a_workflow_violation(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        refused = await transition(lead, item, "block", reason="Why not")

    assert refused.status_code == 409
    assert refused.json()["code"] == "WORKFLOW_VIOLATION"


async def test_a_transition_needs_if_match_and_a_current_one(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        bare = await act(
            asha, item["key"], "transition", {"action": "block", "reason": "Waiting on them"}
        )
        stale = await transition(asha, item, "block", reason="Waiting", version=item["version"] - 1)

    assert bare.status_code == 428
    assert stale.status_code == 412
    assert stale.json()["current"]["version"] == item["version"]


# ----- resolve ----------------------------------------------------------------------------


async def test_resolving_records_the_resolution_the_note_and_the_time(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        resolved = await ok(await transition(asha, item, "resolve", reason="Refund issued"))

    assert (resolved["status"], resolved["resolution"]) == ("resolved", "done")
    assert resolved["resolution_note"] == "Refund issued"
    assert resolved["resolved_at"] is not None
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"], last["reason"]) == (
        "resolved",
        True,
        "Refund issued",
    )
    stored = await row(seeded.url, item["key"])
    assert stored["resolved_at"] is not None


async def test_resolving_needs_a_note(api: httpx.AsyncClient, app_settings: Settings) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        refused = await transition(asha, item, "resolve")

    assert (refused.status_code, refused.json()["code"]) == (422, "REASON_REQUIRED")


async def test_a_payment_investigation_cannot_be_resolved_without_an_approval(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings, type="payment_investigation")
    async with signed_in_as(app_settings, ASHA) as asha:
        refused = await transition(asha, item, "resolve", reason="Looks fine to me")
        state = await get(asha, item["key"])

    body = refused.json()
    assert refused.status_code == 422
    assert body["code"] == "APPROVAL_REQUIRED"
    assert "Request approval" in body["detail"]
    assert state["status"] == "in_progress"
    assert state["version"] == item["version"]  # nothing was written


async def test_resolving_works_once_a_lead_has_approved_and_stops_working_after_an_edit(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings, type="payment_investigation")
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item, "Refund of 40 EUR")
        approved = await ok(await decide(lead, waiting, "approve", note="Checked the ledger"))
        edited = await asha.patch(
            f"/api/v1/items/{item['key']}",
            json={"description": "Changed after the approval"},
            headers={
                "X-CSRF-Token": asha.cookies["baton_csrf"],
                "If-Match": str(approved["version"]),
            },
        )
        refused = await transition(asha, edited.json(), "resolve", reason="Done")

    assert approved["approval"]["status"] == "approved"
    assert edited.status_code == 200
    assert edited.json()["approval"]["status"] == "invalidated"
    assert refused.status_code == 422
    assert refused.json()["code"] == "APPROVAL_REQUIRED"


async def test_an_approved_payment_item_resolves(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings, type="payment_investigation")
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        approved = await ok(await decide(lead, await requested_approval(asha, item), "approve"))
        resolved = await ok(await transition(asha, approved, "resolve", reason="Refund sent"))

    assert resolved["status"] == "resolved"


async def test_a_duplicate_names_the_item_it_duplicates(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    original = await new_item(api, title="The original report")
    item = await new_item(api, title="Same report, filed twice")
    async with signed_in_as(app_settings, PRIYA) as lead:
        missing = await transition(lead, item, "close", reason="Duplicate", resolution="duplicate")
        unknown = await transition(
            lead,
            item,
            "close",
            reason="Duplicate",
            resolution="duplicate",
            duplicate_of="PAY-999999",
        )
        itself = await transition(
            lead,
            item,
            "close",
            reason="Duplicate",
            resolution="duplicate",
            duplicate_of=item["key"],
        )
        done = await ok(
            await transition(
                lead,
                item,
                "close",
                reason="Duplicate of the original",
                resolution="duplicate",
                duplicate_of=original["key"],
            )
        )

    for refused in (missing, unknown, itself):
        assert refused.status_code == 400
        assert refused.json()["errors"][0]["field"] == "duplicate_of"
    assert (done["status"], done["resolution"], done["duplicate_of"]) == (
        "closed",
        "duplicate",
        original["key"],
    )
    stored = await row(seeded.url, item["key"])
    assert stored["duplicate_of_id"] is not None


# ----- reopen -----------------------------------------------------------------------------


async def test_the_requester_reopens_a_resolved_item_and_the_owner_gets_it_back(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        resolved = await ok(await transition(asha, item, "resolve", reason="Fixed"))
    reopened = await ok(await transition(api, resolved, "reopen", reason="Still broken for me"))

    assert (reopened["status"], reopened["assignee"]["name"]) == ("in_progress", "Asha Rao")
    assert (reopened["resolution"], reopened["resolved_at"]) == (None, None)
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"], last["reason"]) == (
        "reopened",
        True,
        "Still broken for me",
    )


async def test_reopening_goes_to_new_if_the_previous_owner_left_the_team(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        resolved = await ok(await transition(asha, item, "resolve", reason="Fixed"))
    await execute(
        seeded.url,
        "DELETE FROM memberships WHERE user_id = (SELECT id FROM users WHERE email = $1) "
        "AND team_id = (SELECT id FROM teams WHERE key = 'PAY')",
        ASHA,
    )
    reopened = await ok(await transition(api, resolved, "reopen", reason="It came back"))

    assert (reopened["status"], reopened["assignee"]) == ("new", None)
    assert reopened["next_step"]["kind"] == "needs_owner"
    await execute(
        seeded.url,
        "INSERT INTO memberships (team_id, user_id, role) SELECT t.id, u.id, 'member' "
        "FROM teams t, users u WHERE t.key = 'PAY' AND u.email = $1",
        ASHA,
    )


async def test_reopening_needs_a_reason_and_the_right_person(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, RAHUL) as rahul:
        resolved = await ok(await transition(asha, item, "resolve", reason="Fixed"))
        no_reason = await transition(api, resolved, "reopen")
        by_other_member = await transition(rahul, resolved, "reopen", reason="Why not")

    assert no_reason.status_code == 422
    assert by_other_member.status_code == 403


async def test_only_a_lead_reopens_a_closed_item(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        closed = await ok(
            await transition(lead, item, "close", reason="Out of scope", resolution="wont_do")
        )
        by_requester = await transition(api, closed, "reopen", reason="Please")
        reopened = await ok(await transition(lead, closed, "reopen", reason="Scope changed"))

    assert by_requester.status_code == 403
    assert (reopened["status"], reopened["assignee"]) == ("new", None)  # nobody owned it


# ----- close and withdraw -----------------------------------------------------------------


async def test_a_lead_closes_an_open_item_with_a_resolution_and_a_reason(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, PRIYA) as lead:
        no_resolution = await transition(lead, item, "close", reason="Not needed")
        no_reason = await transition(lead, item, "close", resolution="wont_do")
        closed = await ok(
            await transition(lead, item, "close", reason="Not needed", resolution="wont_do")
        )

    assert no_resolution.status_code == 400
    assert no_resolution.json()["errors"][0]["field"] == "resolution"
    assert no_reason.status_code == 422
    assert (closed["status"], closed["resolution"]) == ("closed", "wont_do")
    assert closed["closed_at"] is not None


async def test_closing_a_resolved_item_needs_nothing_and_still_leaves_a_reason(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, VIKRAM) as lead:
        resolved = await ok(await transition(asha, item, "resolve", reason="Fixed"))
        closed = await ok(await transition(lead, resolved, "close"))

    assert (closed["status"], closed["resolution"]) == ("closed", "done")
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"], last["reason"]) == (
        "closed",
        True,
        "Closed after resolution",
    )


async def test_the_requester_may_close_a_resolved_item_but_not_an_open_one(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        early = await transition(api, item, "close", reason="Never mind", resolution="wont_do")
        resolved = await ok(await transition(asha, item, "resolve", reason="Fixed"))
    closed = await ok(await transition(api, resolved, "close", reason="Confirmed fixed"))

    assert early.status_code == 403
    assert closed["status"] == "closed"


async def test_an_item_awaiting_approval_cannot_be_closed(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await owned_by_asha(api, app_settings, type="payment_investigation")
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        refused = await transition(lead, waiting, "close", reason="Drop it", resolution="wont_do")

    assert refused.status_code == 409
    assert refused.json()["code"] == "WORKFLOW_VIOLATION"


async def test_the_requester_withdraws_an_item_nobody_has_started(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    no_reason = await transition(api, item, "withdraw")
    withdrawn = await ok(await transition(api, item, "withdraw", reason="Raised by mistake"))

    assert no_reason.status_code == 422
    assert (withdrawn["status"], withdrawn["resolution"]) == ("closed", "wont_do")
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"]) == ("closed", True)
    assert json.loads(last["data"])["resolution"] == {"from": None, "to": "wont_do"}


async def test_withdrawing_is_for_the_requester_and_only_while_new(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    fresh = await new_item(api)
    started = await owned_by_asha(api, app_settings)
    async with signed_in_as(app_settings, PRIYA) as lead:
        by_lead = await transition(lead, fresh, "withdraw", reason="Not mine to withdraw")
    too_late = await transition(api, started, "withdraw", reason="Changed my mind")

    assert by_lead.status_code == 403
    assert too_late.status_code == 403


# ----- history ----------------------------------------------------------------------------


async def test_a_lifecycle_writes_contiguous_versions_and_only_decisions_are_decisions(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        owned = await claimed(asha, item)
        blocked = await ok(await transition(asha, owned, "block", reason="Waiting"))
        working = await ok(await transition(asha, blocked, "unblock"))
        resolved = await ok(await transition(asha, working, "resolve", reason="Fixed"))
        await ok(await transition(lead, resolved, "close"))

    history = await events(seeded.url, item["key"])
    versions = [e["item_version"] for e in history]
    assert versions == sorted(versions)
    assert sorted(set(versions)) == list(range(1, max(versions) + 1))  # no gaps
    assert [e["kind"] for e in history] == [
        "created",
        "assigned",
        "status_changed",
        "blocked",
        "unblocked",
        "resolved",
        "closed",
    ]
    assert [e["kind"] for e in history if e["is_decision"]] == ["resolved", "closed"]
    conn = await seeded.connect()
    try:
        queued = await conn.fetchval(
            "SELECT count(*) FROM outbox "
            "WHERE (payload->'event_ids'->>0)::bigint = ANY($1::bigint[])",
            [e["id"] for e in history],
        )
    finally:
        await conn.close()
    assert queued == len(history)  # one outbox row per event (decision 25)
