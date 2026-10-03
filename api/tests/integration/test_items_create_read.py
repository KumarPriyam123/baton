"""POST /items and GET /items/{key} (SPEC 4.2, 11), through the real app."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from tests.integration.conftest import csrf_headers
from tests.integration.test_teams_members import ADMIN, MEERA, PRIYA, signed_in
from tests.support.items import (
    ITEMS,
    create_body,
    create_item,
    created,
    events_of,
    new_key,
    rows,
    scalar,
    signed_in_as,
)
from tests.support.seeded import SeededDatabase
from tests.support.users import execute

DEV_VIEWER = "dev.viewer@baton.test"  # viewer on PAY and SRE
FARAH = "farah@baton.test"  # member of CMP
ISHAAN = "ishaan.lead@baton.test"  # lead of CMP
SUNITA = "sunita.lead@baton.test"  # lead of SUP


# ----- create -----------------------------------------------------------------------------


async def test_creating_an_item_without_a_session_is_401(api: httpx.AsyncClient) -> None:
    api.cookies.set("baton_csrf", "token")  # pass the CSRF check so authentication is next

    response = await api.post(
        ITEMS, json=create_body(), headers={"X-CSRF-Token": "token", "Idempotency-Key": new_key()}
    )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_create_returns_the_new_item_with_etag_and_location(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)

    response = await create_item(api, title="  Refund stuck for order 48213  ", priority=1)

    assert response.status_code == 201
    item = response.json()
    assert item["key"].startswith("PAY-")
    assert response.headers["ETag"] == '"1"'
    assert response.headers["Location"] == f"/api/v1/items/{item['key']}"
    assert (item["version"], item["status"], item["priority"]) == (1, "new", 1)
    assert item["title"] == "Refund stuck for order 48213"  # trimmed
    assert item["requester"]["name"] == "Meera Iyer"
    assert item["assignee"] is None
    assert item["team"] == {"key": "PAY", "name": "Payments"}
    assert item["approval"] is None
    assert item["next_step"] == {
        "kind": "needs_owner",
        "label": "Needs an owner",
        "severity": "high",
    }
    assert item["last_event"]["kind"] == "created"


async def test_numbers_are_the_teams_counter_and_keys_never_repeat(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)

    first = await created(api)
    second = await created(api)

    assert int(second["key"].split("-")[1]) == int(first["key"].split("-")[1]) + 1


async def test_due_date_comes_from_the_priority_in_calendar_hours(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)

    for priority, hours in {0: 4, 1: 24, 2: 72, 3: 168}.items():
        item = await created(api, priority=priority)

        made = datetime.fromisoformat(item["created_at"])
        assert datetime.fromisoformat(item["due_at"]) - made == timedelta(hours=hours)
        assert item["due_source"] == "auto"


async def test_type_defaults_are_applied_at_creation(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)

    payment = await created(api, type="payment_investigation")
    compliance = await created(api, team_key="CMP", type="compliance_request")
    ops = await created(api, type="ops_task")
    incident = await created(api, type="incident")

    assert (payment["requires_approval"], payment["confidential"]) == (True, False)
    assert (compliance["requires_approval"], compliance["confidential"]) == (True, True)
    assert (ops["requires_approval"], ops["confidential"]) == (False, False)
    assert (incident["requires_approval"], incident["confidential"]) == (False, False)


async def test_an_ops_task_requester_can_ask_for_approval_but_an_incident_cannot(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)

    ops = await created(api, type="ops_task", requires_approval=True)
    refused = await create_item(api, type="incident", requires_approval=True)

    assert ops["requires_approval"] is True
    assert refused.status_code == 400
    assert refused.json()["errors"][0]["field"] == "requires_approval"


async def test_anyone_can_raise_a_request_to_any_team_even_a_viewer(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, DEV_VIEWER)

    item = await created(api, team_key="sre")  # lowercase key is fine

    assert item["team"]["key"] == "SRE"


async def test_creating_writes_one_created_event_at_version_one_plus_watcher_and_outbox(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)

    item = await created(api, priority=0)

    events = await events_of(seeded.url, item["key"])
    assert [(e["kind"], e["item_version"]) for e in events] == [("created", 1)]
    assert events[0]["actor_id"] == uuid.UUID(item["requester"]["id"])
    assert item["last_event_id"] == events[0]["id"]
    watchers = await rows(
        seeded.url,
        "SELECT user_id FROM watchers w JOIN work_items i ON i.id = w.item_id WHERE i.key = $1",
        item["key"],
    )
    assert [w["user_id"] for w in watchers] == [uuid.UUID(item["requester"]["id"])]
    outbox = await rows(
        seeded.url,
        "SELECT payload FROM outbox WHERE topic = 'item.event' AND payload @> $1::jsonb",
        f'{{"event_ids": [{events[0]["id"]}]}}',
    )
    assert len(outbox) == 1


async def test_a_missing_idempotency_key_is_a_400_naming_the_header(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)

    response = await api.post(ITEMS, json=create_body(), headers=csrf_headers(api))

    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "Idempotency-Key"


async def test_bad_input_is_a_400_with_field_errors_and_creates_nothing(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    before = await scalar(seeded.url, "SELECT count(*) FROM work_items")
    bad: list[tuple[dict[str, Any], str]] = [
        ({"title": "ab"}, "title"),
        ({"title": "x" * 201}, "title"),
        ({"title": "ok title\x00"}, "title"),
        ({"description": "y" * 20_001}, "description"),
        ({"description": "has \x00 nul"}, "description"),
        ({"priority": 4}, "priority"),
        ({"priority": -1}, "priority"),
        ({"type": "mystery"}, "type"),
        ({"team_key": "PAYMENTS"}, "team_key"),
        ({"surprise": True}, "surprise"),
    ]

    for overrides, field in bad:
        response = await create_item(api, **overrides)

        assert response.status_code == 400, overrides
        assert response.json()["code"] == "VALIDATION_FAILED"
        assert field in {e["field"] for e in response.json()["errors"]}, overrides

    assert await scalar(seeded.url, "SELECT count(*) FROM work_items") == before


async def test_an_unknown_team_is_a_400_on_team_key(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)

    response = await create_item(api, team_key="ZZZ")

    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "team_key"


# ----- read -------------------------------------------------------------------------------


async def test_get_returns_the_item_with_the_etag_to_send_back(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    response = await api.get(f"{ITEMS}/{item['key'].lower()}")  # keys are not case sensitive

    assert response.status_code == 200
    assert response.headers["ETag"] == f'"{item["version"]}"'
    assert response.json() == item


async def test_a_missing_item_is_404(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)

    response = await api.get(f"{ITEMS}/PAY-999999")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_allowed_actions_follow_the_role_of_the_one_asking(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, type="incident")  # raised by Meera, a PAY viewer
    mine = (await api.get(f"{ITEMS}/{item['key']}")).json()["allowed_actions"]
    async with signed_in_as(app_settings, PRIYA) as lead:
        leads = (await lead.get(f"{ITEMS}/{item['key']}")).json()["allowed_actions"]
    async with signed_in_as(app_settings, DEV_VIEWER) as viewer:
        viewers = (await viewer.get(f"{ITEMS}/{item['key']}")).json()["allowed_actions"]

    # the requester, while the item is new and unassigned
    assert sorted(mine) == sorted(["comment", "watch", "edit_text", "change_priority"])
    assert sorted(leads) == sorted(
        [
            "comment",
            "watch",
            "edit_text",
            "change_priority",
            "edit_type",
            "edit_due_at",
            "edit_confidential",
            "requires_approval_on",
        ]
    )
    assert sorted(viewers) == ["comment", "watch"]


# ----- HTTP-level confidentiality (the phase 3 done-when) ---------------------------------


async def test_confidential_item_is_404_for_a_non_lead_member_and_absent_from_list_and_facets(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)  # a Support member raises a compliance request
    item = await created(api, team_key="CMP", type="compliance_request", title="Data export check")
    key = item["key"]
    assert item["confidential"] is True

    async def look_as(email: str) -> tuple[httpx.Response, list[str], dict[str, int]]:
        async with signed_in_as(app_settings, email) as client:
            one = await client.get(f"{ITEMS}/{key}")
            listed = (await client.get(f"{ITEMS}?limit=100&team=CMP&sort=created")).json()
            facets = (await client.get(f"{ITEMS}/facets?team=CMP")).json()
            assert listed["next_cursor"] is None  # the whole team fits on one page
            return one, [i["key"] for i in listed["items"]], facets["status"]

    member_one, member_list, member_facets = await look_as(FARAH)  # CMP member, not a lead
    lead_one, lead_list, lead_facets = await look_as(ISHAAN)  # CMP lead
    admin_one, admin_list, _ = await look_as(ADMIN)

    assert member_one.status_code == 404  # 404, never 403: its existence does not leak
    assert member_one.json()["code"] == "NOT_FOUND"
    assert key not in member_list
    assert (lead_one.status_code, admin_one.status_code) == (200, 200)
    assert key in lead_list
    assert key in admin_list
    assert (await api.get(f"{ITEMS}/{key}")).status_code == 200  # the requester
    # Counts never include what the list hides: the facet totals equal the visible rows.
    assert sum(member_facets.values()) == len(member_list)
    assert sum(lead_facets.values()) == len(lead_list)
    assert len(lead_list) > len(member_list)
    farah = await scalar(seeded.url, "SELECT id FROM users WHERE email = $1", FARAH)
    assert len(member_list) == await scalar(
        seeded.url,
        "SELECT count(*) FROM work_items i JOIN teams t ON t.id = i.team_id WHERE t.key = 'CMP' "
        "AND (NOT i.confidential OR i.assignee_id = $1 OR i.requester_id = $1)",
        farah,
    )


async def test_the_assignee_of_a_confidential_item_sees_it_but_other_teams_leads_do_not(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, team_key="CMP", type="compliance_request")
    # Make Farah the owner the way the seed does (workflow commands arrive in phase 4).
    await execute(
        seeded.url,
        "UPDATE work_items SET assignee_id = (SELECT id FROM users WHERE email = $1), "
        "status = 'in_progress' WHERE key = $2",
        FARAH,
        item["key"],
    )

    async with signed_in_as(app_settings, FARAH) as farah:
        assert (await farah.get(f"{ITEMS}/{item['key']}")).status_code == 200
    async with signed_in_as(app_settings, SUNITA) as sunita:  # leads Support, not Compliance
        assert (await sunita.get(f"{ITEMS}/{item['key']}")).status_code == 404
