"""Transfer (SPEC 4.3) through the real app."""

import asyncio
import json
from typing import Any

import asyncpg
import httpx

from app.config import Settings
from app.db.urls import asyncpg_dsn
from tests.integration.test_teams_members import signed_in
from tests.support.items import ITEMS, created, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.workflow import (
    ASHA,
    ISHAAN,
    MEERA,
    NEHA,
    PRIYA,
    RAHUL,
    act,
    claimed,
    decide,
    events,
    get,
    ok,
    requested_approval,
    row,
    team_id,
    transition,
)


async def new_item(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    await signed_in(api, MEERA)
    return await created(api, **overrides)


async def transfer(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    team: str,
    reason: str | None = "Wrong team",
    *,
    version: int | None = None,
) -> httpx.Response:
    body: dict[str, Any] = {"team_key": team}
    if reason is not None:
        body["reason"] = reason
    return await act(
        client,
        item["key"],
        "transfer",
        body,
        version=item["version"] if version is None else version,
    )


async def test_a_lead_moves_an_item_and_its_key_does_not_change(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        moved = await ok(await transfer(lead, item, "SRE", "This is an infrastructure problem"))

    assert moved["key"] == item["key"]
    assert moved["team"]["key"] == "SRE"
    assert moved["version"] == item["version"] + 1
    history = [e for e in await events(seeded.url, item["key"]) if e["item_version"] == 2]
    assert [e["kind"] for e in history] == ["transferred"]
    assert history[0]["is_decision"]
    assert history[0]["reason"] == "This is an infrastructure problem"
    sre = await team_id(seeded.url, "SRE")
    assert history[0]["team_id"] == sre  # the event carries the team AFTER the change (SPEC 3.2)
    stored = await row(seeded.url, item["key"])
    assert stored["team_id"] == sre
    assert stored["origin_team_id"] == await team_id(seeded.url, "PAY")


async def test_an_owner_who_works_the_new_team_keeps_the_item_and_one_who_does_not_loses_it(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    kept = await new_item(api)
    lost = await new_item(api)
    async with (
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, RAHUL) as rahul,
        signed_in_as(app_settings, ASHA) as asha,
    ):
        rahul_owns = await claimed(rahul, kept)  # Rahul is a member of PAY and SRE
        asha_owns = await claimed(asha, lost)  # Asha is not in SRE
        stays = await ok(await transfer(lead, rahul_owns, "SRE"))
        unassigned = await ok(await transfer(lead, asha_owns, "SRE"))

    assert (stays["status"], stays["assignee"]["name"]) == ("in_progress", "Rahul Verma")
    assert (unassigned["status"], unassigned["assignee"]) == ("new", None)


async def test_a_blocked_item_whose_owner_cannot_follow_goes_back_to_new(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        blocked = await ok(await transition(asha, owned, "block", reason="Waiting"))
        moved = await ok(await transfer(lead, blocked, "FIN"))

    assert (moved["status"], moved["assignee"]) == ("new", None)
    last = (await events(seeded.url, item["key"]))[-1]
    data = json.loads(last["data"])
    assert last["kind"] == "transferred"
    assert data["status"] == {"from": "blocked", "to": "new"}
    assert data["assignee"]["to"] is None


async def test_the_old_team_loses_sight_and_the_requester_and_new_team_keep_it(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api, title="Visibility changes with the team")
    async with (
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, ASHA) as old_member,
        signed_in_as(app_settings, NEHA) as new_lead,
    ):
        before = await old_member.get(f"{ITEMS}/{item['key']}")
        await ok(await transfer(lead, item, "SRE"))
        after = await old_member.get(f"{ITEMS}/{item['key']}")
        listed = await old_member.get(ITEMS, params={"team": "SRE"})
        new_side = await new_lead.get(f"{ITEMS}/{item['key']}")
    mine = await api.get(f"{ITEMS}/{item['key']}")

    assert before.status_code == 200
    assert after.status_code == 404
    assert item["key"] not in [i["key"] for i in listed.json()["items"]]
    assert new_side.status_code == 200
    assert mine.status_code == 200  # the requester still sees it


async def test_the_lead_who_transferred_gets_the_item_back_with_nothing_to_do_and_then_a_404(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        moved = await ok(await transfer(lead, item, "SRE"))
        later = await lead.get(f"{ITEMS}/{item['key']}")

    assert moved["team"]["key"] == "SRE"
    assert moved["allowed_actions"] == []  # decision 38
    assert later.status_code == 404


async def test_a_waiting_approval_is_cancelled_by_a_transfer(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    async with (
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, RAHUL) as rahul,
    ):
        owned = await claimed(rahul, item)
        waiting = await requested_approval(rahul, owned)
        moved = await ok(await transfer(lead, waiting, "SRE"))
        stored = await asyncpg.connect(asyncpg_dsn(seeded.url))
        try:
            approval = await stored.fetchrow(
                "SELECT status, invalidated_reason, decided_by FROM approvals WHERE id = $1::uuid",
                waiting["approval"]["id"],
            )
        finally:
            await stored.close()

    assert approval is not None
    assert (approval["status"], approval["invalidated_reason"]) == ("cancelled", "transferred")
    assert approval["decided_by"] is None
    assert (moved["status"], moved["assignee"]["name"]) == ("in_progress", "Rahul Verma")
    last = [
        e for e in await events(seeded.url, item["key"]) if e["item_version"] == moved["version"]
    ]
    assert [e["kind"] for e in last] == ["approval_cancelled", "transferred"]
    assert last[0]["reason"] == "transferred"
    assert json.loads(last[0]["data"])["status"] == {
        "from": "awaiting_approval",
        "to": "in_progress",
    }


async def test_an_approved_approval_is_invalidated_by_a_transfer_because_the_team_is_part_of_it(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    async with (
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, RAHUL) as rahul,
    ):
        owned = await claimed(rahul, item)
        approved = await ok(await decide(lead, await requested_approval(rahul, owned), "approve"))
        moved = await ok(await transfer(lead, approved, "SRE"))

    assert moved["approval"]["status"] == "invalidated"
    last = [
        e for e in await events(seeded.url, item["key"]) if e["item_version"] == moved["version"]
    ]
    assert [e["kind"] for e in last] == ["transferred", "approval_invalidated"]
    assert json.loads(last[1]["data"])["fields"] == ["team"]


async def test_the_live_update_notice_names_the_team_the_item_left(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    heard: asyncio.Queue[str] = asyncio.Queue()
    listener = await asyncpg.connect(asyncpg_dsn(seeded.url))
    try:
        await listener.add_listener("item_changes", lambda *args: heard.put_nowait(args[3]))
        async with signed_in_as(app_settings, PRIYA) as lead:
            await ok(await transfer(lead, item, "SRE"))
        notice = json.loads(await asyncio.wait_for(heard.get(), timeout=5))
    finally:
        await listener.close()

    assert notice["prev_team_id"] == str(await team_id(seeded.url, "PAY"))
    assert notice["team_id"] == str(await team_id(seeded.url, "SRE"))


async def test_a_transfer_needs_a_reason_a_real_other_team_and_a_lead(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with (
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, ASHA) as member,
    ):
        no_reason = await transfer(lead, item, "SRE", None)
        unknown = await transfer(lead, item, "ZZZ")
        same = await transfer(lead, item, "PAY")
        by_member = await transfer(member, item, "SRE")
        bare = await act(lead, item["key"], "transfer", {"team_key": "SRE", "reason": "x"})

    assert (no_reason.status_code, no_reason.json()["code"]) == (422, "REASON_REQUIRED")
    for refused in (unknown, same):
        assert refused.status_code == 400
        assert refused.json()["errors"][0]["field"] == "team_key"
    assert by_member.status_code == 403
    assert bare.status_code == 428


async def test_a_lead_of_another_team_cannot_transfer_it(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ISHAAN) as stranger:
        refused = await transfer(stranger, item, "SRE")

    assert refused.status_code == 404  # a Compliance lead cannot even see a Payments item


async def test_a_resolved_or_closed_item_cannot_be_transferred(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        resolved = await ok(await transition(asha, owned, "resolve", reason="Done"))
        refused = await transfer(lead, resolved, "SRE")

    assert refused.status_code == 409
    assert refused.json()["code"] == "WORKFLOW_VIOLATION"


async def test_the_state_after_a_transfer_is_readable_by_the_new_team(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, NEHA) as neha:
        await ok(await transfer(lead, item, "SRE"))
        view = await get(neha, item["key"])

    assert view["next_step"]["kind"] == "needs_owner"
    assert "claim" in view["allowed_actions"]  # Neha leads SRE, and leads may claim
    assert "assign" in view["allowed_actions"]
