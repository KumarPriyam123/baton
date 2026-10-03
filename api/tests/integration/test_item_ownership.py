"""Claim, release, assign and unassign (SPEC 4.1, 6.1) through the real app."""

import json
from typing import Any

import httpx

from app.config import Settings
from tests.integration.test_teams_members import signed_in
from tests.support.items import created, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.workflow import (
    ASHA,
    DEV_VIEWER,
    MEERA,
    PRIYA,
    RAHUL,
    VIKRAM,
    act,
    claimed,
    events,
    get,
    ok,
    requested_approval,
    row,
    transition,
)

SAMEER = "sameer@baton.test"  # Finance member, Compliance viewer: nothing to do with PAY


async def new_item(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    await signed_in(api, MEERA)
    return await created(api, **overrides)


# ----- claim ------------------------------------------------------------------------------


async def test_a_member_claims_a_new_item_and_it_is_one_version_and_two_events(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha:
        result = await ok(await act(asha, item["key"], "claim"))

    assert (result["status"], result["version"]) == ("in_progress", 2)
    assert result["assignee"]["name"] == "Asha Rao"
    assert "release" in result["allowed_actions"]
    written = [e for e in await events(seeded.url, item["key"]) if e["item_version"] == 2]
    assert [e["kind"] for e in written] == ["assigned", "status_changed"]
    assert json.loads(written[1]["data"]) == {"status": {"from": "new", "to": "in_progress"}}
    watchers = await seeded.connect()
    try:
        row_ = await watchers.fetchval(
            "SELECT count(*) FROM watchers w JOIN work_items i ON i.id = w.item_id "
            "JOIN users u ON u.id = w.user_id WHERE i.key = $1 AND u.email = $2",
            item["key"],
            ASHA,
        )
    finally:
        await watchers.close()
    assert row_ == 1


async def test_claiming_my_own_item_again_is_a_200_that_changes_nothing(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha:
        first = await claimed(asha, item)
        again = await act(asha, item["key"], "claim")

    assert again.status_code == 200
    assert again.json()["version"] == first["version"]
    assert len(await events(seeded.url, item["key"])) == 3  # created, assigned, status_changed


async def test_claiming_an_item_someone_else_owns_is_409_with_who_and_when(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, RAHUL) as rahul:
        await claimed(asha, item)
        late = await act(rahul, item["key"], "claim")

    body = late.json()
    assert late.status_code == 409
    assert body["code"] == "ALREADY_CLAIMED"
    assert body["claimed_by"]["name"] == "Asha Rao"
    assert body["claimed_at"]
    assert body["current"]["assignee"]["name"] == "Asha Rao"
    assert "Asha Rao" in body["detail"]


async def test_who_may_claim(api: httpx.AsyncClient, app_settings: Settings) -> None:
    item = await new_item(api)
    mine = await act(api, item["key"], "claim")  # Meera raised it; she is only a PAY viewer
    async with (
        signed_in_as(app_settings, DEV_VIEWER) as viewer,
        signed_in_as(app_settings, SAMEER) as outsider,
    ):
        by_viewer = await act(viewer, item["key"], "claim")
        by_outsider = await act(outsider, item["key"], "claim")

    assert mine.status_code == 403
    assert by_viewer.status_code == 403
    assert "Only members and leads" in by_viewer.json()["detail"]
    assert by_outsider.status_code == 404  # not visible: it does not exist for them


async def test_a_claim_on_a_resolved_item_says_why_not(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, RAHUL) as rahul:
        owned = await claimed(asha, item)
        resolved = await ok(await transition(asha, owned, "resolve", reason="Fixed it"))
        mine_again = await act(asha, item["key"], "claim")
        theirs = await act(rahul, item["key"], "claim")

    assert resolved["status"] == "resolved"
    assert mine_again.status_code == 409
    assert mine_again.json()["code"] == "WORKFLOW_VIOLATION"
    assert theirs.status_code == 409
    assert theirs.json()["code"] == "WORKFLOW_VIOLATION"  # resolved is not "taken": it is done


# ----- release ----------------------------------------------------------------------------


async def test_the_owner_releases_and_it_returns_to_new(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        released = await ok(await act(asha, item["key"], "release"))

    assert (released["status"], released["assignee"]) == ("new", None)
    assert released["version"] == owned["version"] + 1
    last = [e for e in await events(seeded.url, item["key"]) if e["item_version"] == 3]
    assert [e["kind"] for e in last] == ["unassigned", "status_changed"]
    assert (await row(seeded.url, item["key"]))["assignee_id"] is None


async def test_only_the_owner_releases(api: httpx.AsyncClient, app_settings: Settings) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        await claimed(asha, item)
        by_lead = await act(lead, item["key"], "release")  # a lead unassigns, not releases
        by_other = await act(api, item["key"], "release")

    assert by_lead.status_code == 403
    assert "Only the assignee" in by_lead.json()["detail"]
    assert by_other.status_code == 403


async def test_release_is_refused_while_an_approval_is_waiting(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    async with signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        waiting = await requested_approval(asha, owned)
        refused = await act(asha, item["key"], "release")
        state = await get(asha, item["key"])

    assert waiting["status"] == "awaiting_approval"
    assert refused.status_code == 409
    assert refused.json()["code"] == "WORKFLOW_VIOLATION"
    assert "Cancel the approval request first" in refused.json()["detail"]
    assert state["version"] == waiting["version"]  # nothing changed


# ----- assign and unassign ----------------------------------------------------------------


async def test_a_lead_assigns_a_new_item_and_it_starts(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        who = await lead.get("/api/v1/users", params={"q": "asha"})
        asha = who.json()["items"][0]["id"]
        result = await ok(
            await act(lead, item["key"], "assign", {"assignee_id": asha}, version=item["version"])
        )

    assert (result["status"], result["assignee"]["name"]) == ("in_progress", "Asha Rao")
    assert result["version"] == item["version"] + 1
    written = [e for e in await events(seeded.url, item["key"]) if e["item_version"] == 2]
    assert [e["kind"] for e in written] == ["assigned", "status_changed"]


async def test_assign_needs_if_match_and_a_current_one(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        users = (await lead.get("/api/v1/users", params={"q": "rahul"})).json()["items"]
        target = users[0]["id"]
        bare = await act(lead, item["key"], "assign", {"assignee_id": target})
        await claimed(asha, item)  # the item moves on under the lead
        stale = await act(
            lead, item["key"], "assign", {"assignee_id": target}, version=item["version"]
        )

    assert bare.status_code == 428
    assert stale.status_code == 412
    assert stale.json()["code"] == "VERSION_CONFLICT"
    assert [e["kind"] for e in stale.json()["changes_since"]] == ["assigned", "status_changed"]


async def test_assigning_someone_who_cannot_work_the_team_is_refused(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        viewer = (await lead.get("/api/v1/users", params={"q": "dev.viewer"})).json()["items"][0]
        outsider = (await lead.get("/api/v1/users", params={"q": "sameer"})).json()["items"][0]
        to_viewer = await act(
            lead, item["key"], "assign", {"assignee_id": viewer["id"]}, version=item["version"]
        )
        to_outsider = await act(
            lead, item["key"], "assign", {"assignee_id": outsider["id"]}, version=item["version"]
        )

    for refused in (to_viewer, to_outsider):
        assert refused.status_code == 409
        assert refused.json()["code"] == "WORKFLOW_VIOLATION"
        assert "Only members and leads" in refused.json()["detail"]


async def test_a_member_cannot_assign_others(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha:
        me = (await asha.get("/api/v1/me")).json()["user"]["id"]
        refused = await act(
            asha, item["key"], "assign", {"assignee_id": me}, version=item["version"]
        )

    assert refused.status_code == 403
    assert "Only leads" in refused.json()["detail"]


async def test_assigning_the_current_owner_changes_nothing(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        me = (await asha.get("/api/v1/me")).json()["user"]["id"]
        again = await act(
            lead, item["key"], "assign", {"assignee_id": me}, version=owned["version"]
        )

    assert again.status_code == 200
    assert again.json()["version"] == owned["version"]


async def test_reassigning_a_blocked_item_keeps_it_blocked(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        blocked = await ok(await transition(asha, owned, "block", reason="Waiting on the bank"))
        rahul = (await lead.get("/api/v1/users", params={"q": "rahul"})).json()["items"][0]
        moved = await ok(
            await act(
                lead,
                item["key"],
                "assign",
                {"assignee_id": rahul["id"]},
                version=blocked["version"],
            )
        )

    assert (moved["status"], moved["assignee"]["name"]) == ("blocked", "Rahul Verma")


async def test_a_lead_unassigns_an_active_item_back_to_new(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, VIKRAM) as lead, signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        cleared = await ok(
            await act(
                lead,
                item["key"],
                "assign",
                {"assignee_id": None, "reason": "Asha is on leave"},
                version=owned["version"],
            )
        )

    assert (cleared["status"], cleared["assignee"]) == ("new", None)
    last = [e for e in await events(seeded.url, item["key"]) if e["item_version"] == 3]
    assert [(e["kind"], e["reason"]) for e in last] == [
        ("unassigned", "Asha is on leave"),
        ("status_changed", None),
    ]


async def test_unassigning_an_item_nobody_owns_is_a_workflow_violation(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        refused = await act(
            lead, item["key"], "assign", {"assignee_id": None}, version=item["version"]
        )

    assert refused.status_code == 409
    assert refused.json()["code"] == "WORKFLOW_VIOLATION"


async def test_assign_and_unassign_are_refused_while_an_approval_is_waiting(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        waiting = await requested_approval(asha, await claimed(asha, item))
        rahul = (await lead.get("/api/v1/users", params={"q": "rahul"})).json()["items"][0]
        reassign = await act(
            lead,
            item["key"],
            "assign",
            {"assignee_id": rahul["id"]},
            version=waiting["version"],
        )
        unassign = await act(
            lead, item["key"], "assign", {"assignee_id": None}, version=waiting["version"]
        )

    for refused in (reassign, unassign):
        assert refused.status_code == 409
        assert "Cancel the approval request first" in refused.json()["detail"]
