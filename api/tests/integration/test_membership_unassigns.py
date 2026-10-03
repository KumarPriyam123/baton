"""SPEC 4.3b: removing or demoting someone unassigns their open items in the same transaction."""

import uuid
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.repo import teams as teams_repo
from tests.integration.conftest import csrf_headers, sign_in
from tests.integration.test_teams_members import MEERA, PRIYA, new_user, signed_in, team_id
from tests.support.items import created, rows
from tests.support.seeded import SeededDatabase
from tests.support.users import PASSWORD
from tests.support.workflow import act, claimed, events, ok, requested_approval, row, transition


async def worker_with_items(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> tuple[uuid.UUID, dict[str, dict[str, Any]]]:
    """A new PAY member who owns one item in each open state, plus a resolved one, and one more in
    SRE (another team they also belong to)."""
    pay, sre = await team_id(seeded, "PAY"), await team_id(seeded, "SRE")
    email, user_id = await new_user(
        seeded, name="Worker Bee", memberships={pay: "member", sre: "member"}
    )
    await signed_in(api, MEERA)
    async with app_client(app_settings) as me:
        assert (await sign_in(me, email, PASSWORD)).status_code == 200
        items: dict[str, dict[str, Any]] = {}
        for name, overrides in {
            "in_progress": {},
            "blocked": {},
            "awaiting": {"type": "payment_investigation"},
            "resolved": {},
            "sre": {"team_key": "SRE"},
        }.items():
            items[name] = await claimed(me, await created(api, **overrides))
        items["blocked"] = await ok(
            await transition(me, items["blocked"], "block", reason="Waiting")
        )
        items["awaiting"] = await requested_approval(me, items["awaiting"])
        items["resolved"] = await ok(
            await transition(me, items["resolved"], "resolve", reason="Done")
        )
    return user_id, items


def app_client(settings: Settings) -> Any:
    from tests.support.app import running_app

    return running_app(settings)


async def test_removing_a_member_sends_every_open_item_they_own_back_to_new(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    user_id, items = await worker_with_items(api, app_settings, seeded)
    await signed_in(api, PRIYA)

    response = await api.delete(f"/api/v1/teams/PAY/members/{user_id}", headers=csrf_headers(api))

    assert response.status_code == 204
    for name in ("in_progress", "blocked", "awaiting"):
        stored = await row(seeded.url, items[name]["key"])
        assert (stored["status"], stored["assignee_id"]) == ("new", None), name
        assert stored["version"] == items[name]["version"] + 1, name  # one command, one bump
        last = [
            e
            for e in await events(seeded.url, items[name]["key"])
            if e["item_version"] == stored["version"]
        ]
        assert last[-2]["kind"] == "unassigned", name
        assert last[-2]["reason"] == "Removed from Payments", name
        assert last[-1]["kind"] == "status_changed", name
    # The resolved item is not open work: it keeps its owner. Another team's item is untouched.
    assert (await row(seeded.url, items["resolved"]["key"]))["assignee_id"] == user_id
    assert (await row(seeded.url, items["sre"]["key"]))["assignee_id"] == user_id


async def test_an_item_awaiting_approval_has_its_request_cancelled_first_with_the_same_reason(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    user_id, items = await worker_with_items(api, app_settings, seeded)
    await signed_in(api, PRIYA)
    key = items["awaiting"]["key"]

    await api.delete(f"/api/v1/teams/PAY/members/{user_id}", headers=csrf_headers(api))

    version = (await row(seeded.url, key))["version"]
    written = [e for e in await events(seeded.url, key) if e["item_version"] == version]
    assert [e["kind"] for e in written] == ["approval_cancelled", "unassigned", "status_changed"]
    assert written[0]["reason"] == "Removed from Payments"
    (approval,) = await rows(
        seeded.url,
        "SELECT a.status::text AS status, a.invalidated_reason FROM approvals a "
        "JOIN work_items w ON w.id = a.item_id WHERE w.key = $1",
        key,
    )
    assert (approval["status"], approval["invalidated_reason"]) == (
        "cancelled",
        "Removed from Payments",
    )
    leads = await rows(seeded.url, "SELECT id FROM users WHERE email = $1", PRIYA)
    assert {e["actor_id"] for e in written} == {leads[0]["id"]}  # the lead who removed them


async def test_demoting_to_viewer_does_the_same(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    user_id, items = await worker_with_items(api, app_settings, seeded)
    await signed_in(api, PRIYA)

    response = await api.patch(
        f"/api/v1/teams/PAY/members/{user_id}", json={"role": "viewer"}, headers=csrf_headers(api)
    )

    assert response.status_code == 200
    assert response.json()["role"] == "viewer"
    for name in ("in_progress", "blocked", "awaiting"):
        assert (await row(seeded.url, items[name]["key"]))["assignee_id"] is None, name
    assert (await row(seeded.url, items["sre"]["key"]))["assignee_id"] == user_id


async def test_demoting_a_lead_to_member_unassigns_nothing(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    pay = await team_id(seeded, "PAY")
    _, lead_id = await new_user(seeded, memberships={pay: "lead"})
    await signed_in(api, MEERA)
    item = await created(api)
    await signed_in(api, "admin@baton.test")
    await seeded_assign(seeded, item["key"], lead_id)

    response = await api.patch(
        f"/api/v1/teams/PAY/members/{lead_id}", json={"role": "member"}, headers=csrf_headers(api)
    )

    assert response.status_code == 200
    assert (await row(seeded.url, item["key"]))["assignee_id"] == lead_id


async def seeded_assign(seeded: SeededDatabase, key: str, user_id: uuid.UUID) -> None:
    conn = await seeded.connect()
    try:
        await conn.execute(
            "UPDATE work_items SET assignee_id = $1, status = 'in_progress' WHERE key = $2",
            user_id,
            key,
        )
    finally:
        await conn.close()


async def test_a_failure_part_way_leaves_the_member_and_every_item_as_they_were(
    api: httpx.AsyncClient,
    seeded: SeededDatabase,
    app_settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, items = await worker_with_items(api, app_settings, seeded)
    await signed_in(api, PRIYA)

    async def crash(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom after the items were unassigned")

    monkeypatch.setattr(teams_repo, "delete_membership", crash)

    response = await api.delete(f"/api/v1/teams/PAY/members/{user_id}", headers=csrf_headers(api))

    assert response.status_code == 500
    for name in ("in_progress", "blocked", "awaiting"):
        stored = await row(seeded.url, items[name]["key"])
        assert stored["assignee_id"] == user_id, name  # one transaction: nothing half-done
        assert stored["version"] == items[name]["version"], name
    (approval,) = await rows(
        seeded.url,
        "SELECT a.status::text AS status FROM approvals a JOIN work_items w ON w.id = a.item_id "
        "WHERE w.key = $1",
        items["awaiting"]["key"],
    )
    assert approval["status"] == "pending"


async def test_removing_someone_who_owns_nothing_just_removes_them(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    pay = await team_id(seeded, "PAY")
    _, idle = await new_user(seeded, memberships={pay: "member"})
    await signed_in(api, PRIYA)

    response = await api.delete(f"/api/v1/teams/PAY/members/{idle}", headers=csrf_headers(api))

    assert response.status_code == 204


async def test_a_removed_member_can_no_longer_claim_in_the_team(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    user_id, _ = await worker_with_items(api, app_settings, seeded)
    await signed_in(api, PRIYA)
    await api.delete(f"/api/v1/teams/PAY/members/{user_id}", headers=csrf_headers(api))
    await signed_in(api, MEERA)
    fresh = await created(api)
    email = (await rows(seeded.url, "SELECT email::text AS e FROM users WHERE id = $1", user_id))[
        0
    ]["e"]

    async with app_client(app_settings) as removed:
        assert (await sign_in(removed, email, PASSWORD)).status_code == 200
        refused = await act(removed, fresh["key"], "claim")

    assert refused.status_code in (403, 404)  # no longer in the team: PAY items are not theirs
