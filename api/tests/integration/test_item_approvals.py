"""Request, approve, reject, cancel and invalidate approvals (SPEC 4.4) through the real app."""

import json
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.domain.hashing import subject_hash
from app.services import commands
from tests.integration.test_teams_members import signed_in
from tests.support.items import ITEMS, created, patch_item, rows, signed_in_as
from tests.support.seeded import SeededDatabase
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
    team_id,
)


async def payment_in_progress(api: httpx.AsyncClient, app_settings: Settings) -> dict[str, Any]:
    """A payment investigation (approval required) that Asha owns."""
    await signed_in(api, MEERA)
    item = await created(api, type="payment_investigation", title="Refund stuck for order 48213")
    async with signed_in_as(app_settings, ASHA) as asha:
        return await claimed(asha, item)


# ----- request ----------------------------------------------------------------------------


async def test_the_owner_requests_approval_and_it_is_bound_to_the_content(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        waiting = await requested_approval(asha, item, "Refund of 40 EUR, ledger attached")

    assert waiting["status"] == "awaiting_approval"
    assert waiting["version"] == item["version"] + 1
    assert waiting["approval"]["status"] == "pending"
    assert waiting["approval"]["requested_by"]["name"] == "Asha Rao"
    (approval,) = await rows(
        seeded.url,
        "SELECT a.* FROM approvals a JOIN work_items w ON w.id = a.item_id WHERE w.key = $1",
        item["key"],
    )
    expected = subject_hash(
        await team_id(seeded.url, "PAY"),
        "payment_investigation",
        item["title"],
        item["description"],
    )
    assert approval["subject_hash"] == expected
    assert approval["request_note"] == "Refund of 40 EUR, ledger attached"
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["reason"], last["is_decision"]) == (
        "approval_requested",
        "Refund of 40 EUR, ledger attached",
        False,
    )
    assert json.loads(last["data"])["approval_id"] == waiting["approval"]["id"]


async def test_only_the_owner_or_a_lead_requests_and_only_from_in_progress(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    new = await created(api, type="payment_investigation")
    async with (
        signed_in_as(app_settings, RAHUL) as rahul,
        signed_in_as(app_settings, PRIYA) as lead,
    ):
        by_other_member = await act(rahul, item["key"], "approvals", {}, version=item["version"])
        from_new = await act(lead, new["key"], "approvals", {}, version=new["version"])
        by_lead = await act(lead, item["key"], "approvals", {}, version=item["version"])

    assert by_other_member.status_code == 403
    assert from_new.status_code == 409
    assert from_new.json()["code"] == "WORKFLOW_VIOLATION"
    assert by_lead.status_code == 201  # a lead may ask on the owner's behalf


async def test_a_second_request_while_one_waits_is_409_whatever_version_it_carries(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        waiting = await requested_approval(asha, item)
        same_view = await act(asha, item["key"], "approvals", {}, version=item["version"])
        fresh = await act(asha, item["key"], "approvals", {}, version=waiting["version"])

    for refused in (same_view, fresh):
        assert refused.status_code == 409
        assert refused.json()["code"] == "APPROVAL_ALREADY_PENDING"


async def test_a_request_needs_if_match(api: httpx.AsyncClient, app_settings: Settings) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        bare = await act(asha, item["key"], "approvals", {})
        stale = await act(asha, item["key"], "approvals", {}, version=item["version"] - 1)

    assert bare.status_code == 428
    assert stale.status_code == 412


# ----- decide -----------------------------------------------------------------------------


async def test_another_lead_approves_and_the_item_returns_to_in_progress(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        view = await get(lead, item["key"])
        approved = await ok(await decide(lead, waiting, "approve", note="Checked the ledger"))

    assert "approve" in view["allowed_actions"]
    assert view["next_step"]["label"] == "Needs your approval"
    assert (approved["status"], approved["approval"]["status"]) == ("in_progress", "approved")
    assert approved["approval"]["decided_by"]["name"] == "Priya Nair"
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"], last["reason"]) == (
        "approval_approved",
        True,
        "Checked the ledger",
    )
    (stored,) = await rows(
        seeded.url,
        "SELECT decided_by, decided_at, decision_note FROM approvals WHERE id = $1::uuid",
        approved["approval"]["id"],
    )
    assert stored["decision_note"] == "Checked the ledger"
    assert stored["decided_by"] is not None


async def test_an_approval_without_a_note_still_leaves_a_reason_on_the_decision(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        await ok(await decide(lead, waiting, "approve"))

    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["reason"], last["is_decision"]) == (
        "approval_approved",
        "Approved",
        True,
    )


async def test_the_lead_who_asked_cannot_decide_their_own_request(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, PRIYA) as lead:
        waiting = await ok(
            await act(lead, item["key"], "approvals", {}, version=item["version"]), 201
        )
        view = await get(lead, item["key"])
        approve = await decide(lead, waiting, "approve")
        reject = await decide(lead, waiting, "reject", note="No")
        cancel = await act(lead, item["key"], f"approvals/{waiting['approval']['id']}/cancel")

    assert "approve" not in view["allowed_actions"]
    assert "cancel_approval" in view["allowed_actions"]
    for refused in (approve, reject):
        assert refused.status_code == 403
        assert "You requested this approval" in refused.json()["detail"]
    assert cancel.status_code == 200


async def test_the_database_refuses_a_self_approval_even_if_the_policy_is_bypassed(
    api: httpx.AsyncClient, app_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, PRIYA) as lead:
        waiting = await ok(
            await act(lead, item["key"], "approvals", {}, version=item["version"]), 201
        )
        monkeypatch.setattr(commands, "_authorize", lambda *args, **kwargs: None)
        bypassed = await decide(lead, waiting, "approve")
        state = await get(lead, item["key"])

    assert bypassed.status_code == 403  # approvals_four_eyes, mapped by db/errors.py
    assert "You requested this approval" in bypassed.json()["detail"]
    assert state["approval"]["status"] == "pending"  # rolled back: nothing was approved
    assert state["version"] == waiting["version"]


async def test_only_leads_decide(api: httpx.AsyncClient, app_settings: Settings) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, RAHUL) as rahul:
        waiting = await requested_approval(asha, item)
        by_owner = await decide(asha, waiting, "approve")
        by_member = await decide(rahul, waiting, "approve")

    assert by_owner.status_code == 403
    assert by_member.status_code == 403
    assert "Only leads" in by_member.json()["detail"]


async def test_a_rejection_needs_a_note_and_sends_the_item_back_to_its_owner(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, VIKRAM) as lead:
        waiting = await requested_approval(asha, item)
        bare = await decide(lead, waiting, "reject")
        rejected = await ok(await decide(lead, waiting, "reject", note="Amount does not match"))

    assert (bare.status_code, bare.json()["code"]) == (422, "REASON_REQUIRED")
    assert (rejected["status"], rejected["approval"]["status"]) == ("in_progress", "rejected")
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["reason"], last["is_decision"]) == (
        "approval_rejected",
        "Amount does not match",
        True,
    )


async def test_deciding_on_a_stale_view_is_412_and_approves_nothing(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        reviewed = await get(lead, item["key"])  # the lead opens it...
        moved = await patch_item(asha, item["key"], {"priority": 0}, version=waiting["version"])
        late = await decide(lead, reviewed, "approve")  # ...and clicks Approve afterwards
        state = await get(lead, item["key"])

    assert moved.status_code == 200
    assert late.status_code == 412
    body = late.json()
    assert body["code"] == "VERSION_CONFLICT"
    assert [e["kind"] for e in body["changes_since"]] == ["priority_changed"]
    assert state["approval"]["status"] == "pending"
    assert state["status"] == "awaiting_approval"


async def test_deciding_without_if_match_is_428(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        bare = await act(
            lead,
            item["key"],
            f"approvals/{waiting['approval']['id']}/decision",
            {"decision": "approve"},
        )

    assert bare.status_code == 428


async def test_an_approval_id_from_another_item_is_404(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    other = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        refused = await act(
            lead,
            other["key"],
            f"approvals/{waiting['approval']['id']}/decision",
            {"decision": "approve"},
            version=other["version"],
        )

    assert refused.status_code == 404


# ----- cancel -----------------------------------------------------------------------------


async def test_the_person_who_asked_cancels_and_nobody_else_of_that_rank_can(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with (
        signed_in_as(app_settings, ASHA) as asha,
        signed_in_as(app_settings, RAHUL) as rahul,
        signed_in_as(app_settings, PRIYA) as lead,
    ):
        waiting = await requested_approval(asha, item)
        path = f"approvals/{waiting['approval']['id']}/cancel"
        by_other = await act(rahul, item["key"], path)
        cancelled = await ok(await act(asha, item["key"], path))
        by_requester_again = await act(asha, item["key"], path)
        by_lead_again = await act(lead, item["key"], path)

    assert by_other.status_code == 403
    assert (cancelled["status"], cancelled["approval"]["status"]) == ("in_progress", "cancelled")
    assert cancelled["version"] == waiting["version"] + 1
    assert by_requester_again.status_code == 403  # nothing is waiting, so nobody "asked"
    assert by_lead_again.status_code == 409  # a lead may cancel, but there is nothing to cancel
    last = (await events(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"]) == ("approval_cancelled", False)


async def test_a_lead_may_cancel_someone_elses_request(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        cancelled = await ok(
            await act(lead, item["key"], f"approvals/{waiting['approval']['id']}/cancel")
        )

    assert cancelled["approval"]["status"] == "cancelled"


# ----- invalidation (SPEC 4.4) ------------------------------------------------------------


async def test_editing_the_content_of_a_waiting_request_invalidates_it(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, item)
        reviewed = await get(lead, item["key"])
        edited = await patch_item(
            asha,
            item["key"],
            {"description": "Refund of 400 EUR, not 40"},
            version=waiting["version"],
        )
        late = await decide(lead, reviewed, "approve")
        fresh = await get(lead, item["key"])
        on_stale_id = await decide(lead, fresh | {"approval": reviewed["approval"]}, "approve")

    assert edited.status_code == 200
    body = edited.json()
    assert (body["status"], body["approval"]["status"]) == ("in_progress", "invalidated")
    assert body["version"] == waiting["version"] + 1
    assert late.status_code == 412  # the lead was reviewing the old text
    assert on_stale_id.status_code == 409  # even with a fresh version, that request is gone
    written = [
        e for e in await events(seeded.url, item["key"]) if e["item_version"] == body["version"]
    ]
    assert [e["kind"] for e in written] == ["field_changed", "approval_invalidated"]
    assert written[1]["is_decision"]
    data = json.loads(written[1]["data"])
    assert data["fields"] == ["description"]
    assert data["status"] == {"from": "awaiting_approval", "to": "in_progress"}
    assert data["approval_ids"] == [waiting["approval"]["id"]]


async def test_editing_the_content_after_approval_invalidates_it_and_a_new_one_is_needed(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        approved = await ok(await decide(lead, await requested_approval(asha, item), "approve"))
        edited = await ok(
            await patch_item(
                asha,
                item["key"],
                {"title": "Refund stuck for order 48214"},
                version=approved["version"],
            )
        )
        second = await requested_approval(asha, edited)
        again = await ok(await decide(lead, second, "approve"))

    assert (edited["status"], edited["approval"]["status"]) == ("in_progress", "invalidated")
    assert again["approval"]["status"] == "approved"
    assert again["approval"]["id"] != approved["approval"]["id"]


async def test_changing_the_type_invalidates_an_approval_too(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        approved = await ok(await decide(lead, await requested_approval(asha, item), "approve"))
        retyped = await ok(
            await patch_item(lead, item["key"], {"type": "incident"}, version=approved["version"])
        )

    assert retyped["approval"]["status"] == "invalidated"


async def test_requesting_approval_for_the_same_content_after_it_was_approved_is_allowed(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        approved = await ok(await decide(lead, await requested_approval(asha, item), "approve"))
        asked_again = await act(asha, item["key"], "approvals", {}, version=approved["version"])

    assert asked_again.status_code == 201  # SPEC 4.1 asks only for "no pending approval"


async def test_the_item_endpoint_shows_the_newest_approval_and_the_url_helper_agrees(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await payment_in_progress(api, app_settings)
    async with signed_in_as(app_settings, ASHA) as asha:
        waiting = await requested_approval(asha, item)
        listed = await asha.get(ITEMS, params={"status": "awaiting_approval", "assignee": "me"})

    assert waiting["key"] in [i["key"] for i in listed.json()["items"]]
