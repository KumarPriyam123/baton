"""GET /items/{key}/events (SPEC 11): the timeline, paged by event id."""

import httpx

from app.config import Settings
from tests.integration.test_items_create_read import DEV_VIEWER
from tests.integration.test_teams_members import ASHA, MEERA, PRIYA, signed_in
from tests.support.items import ITEMS, created, edited, signed_in_as


async def history_of_three(client: httpx.AsyncClient) -> dict[str, object]:
    item = await created(client, priority=2)
    item = await edited(client, item, {"title": "Renamed once for the timeline"})
    return await edited(client, item, {"title": "Renamed twice for the timeline", "priority": 1})


async def test_the_timeline_is_oldest_first_by_default_with_actor_and_data(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)
    item = await history_of_three(api)

    page = (await api.get(f"{ITEMS}/{item['key']}/events")).json()

    assert [(e["kind"], e["item_version"]) for e in page["items"]] == [
        ("created", 1),
        ("field_changed", 2),
        ("field_changed", 3),
        ("priority_changed", 3),
    ]
    assert page["next_cursor"] is None
    ids = [e["id"] for e in page["items"]]
    assert ids == sorted(ids)
    assert {e["actor"]["name"] for e in page["items"]} == {"Meera Iyer"}
    assert page["items"][1]["data"]["title"]["to"] == "Renamed once for the timeline"


async def test_paging_forward_with_after_event_id_visits_every_event_once(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)
    item = await history_of_three(api)
    seen: list[int] = []
    after = 0

    while True:
        page = (
            await api.get(f"{ITEMS}/{item['key']}/events?limit=3&after_event_id={after}")
        ).json()
        assert len(page["items"]) <= 3
        seen += [e["id"] for e in page["items"]]
        if page["next_cursor"] is None:
            break
        after = page["next_cursor"]

    full = (await api.get(f"{ITEMS}/{item['key']}/events")).json()["items"]
    assert seen == [e["id"] for e in full]
    assert len(seen) == len(set(seen)) == 4


async def test_newest_first_pages_walk_back_to_the_start(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await history_of_three(api)
    ids: list[int] = []
    after = 0

    while True:
        page = (
            await api.get(f"{ITEMS}/{item['key']}/events?order=desc&limit=3&after_event_id={after}")
        ).json()
        ids += [e["id"] for e in page["items"]]
        if page["next_cursor"] is None:
            break
        after = page["next_cursor"]

    oldest_first = [
        e["id"] for e in (await api.get(f"{ITEMS}/{item['key']}/events")).json()["items"]
    ]
    assert ids == list(reversed(oldest_first))


async def test_after_the_last_event_there_is_nothing(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await history_of_three(api)

    page = (
        await api.get(f"{ITEMS}/{item['key']}/events?after_event_id={item['last_event_id']}")
    ).json()

    assert page == {"items": [], "next_cursor": None}


async def test_a_priority_lowering_decision_shows_its_reason_in_the_timeline(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, priority=1)
    async with signed_in_as(app_settings, PRIYA) as lead:
        await edited(lead, item, {"priority": 3, "reason": "Customer says it works now"})
        page = (await lead.get(f"{ITEMS}/{item['key']}/events")).json()

    decision = page["items"][-1]
    assert (decision["kind"], decision["is_decision"]) == ("priority_changed", True)
    assert decision["reason"] == "Customer says it works now"
    assert decision["actor"]["name"] == "Priya Nair"


async def test_events_follow_the_same_visibility_as_the_item(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    hidden = await created(api, team_key="CMP", type="compliance_request")
    visible = await created(api, team_key="PAY")

    async with signed_in_as(app_settings, ASHA) as outsider:  # Payments member, not in CMP
        gone = await outsider.get(f"{ITEMS}/{hidden['key']}/events")
        seen = await outsider.get(f"{ITEMS}/{visible['key']}/events")
        missing = await outsider.get(f"{ITEMS}/PAY-999999/events")
    async with signed_in_as(app_settings, DEV_VIEWER) as viewer:
        watched = await viewer.get(f"{ITEMS}/{visible['key']}/events")

    assert gone.status_code == 404
    assert gone.json()["code"] == "NOT_FOUND"
    assert missing.status_code == 404
    assert (seen.status_code, watched.status_code) == (200, 200)


async def test_events_need_a_session_and_a_sane_limit(api: httpx.AsyncClient) -> None:
    assert (await api.get(f"{ITEMS}/PAY-1/events")).status_code == 401
    await signed_in(api, MEERA)
    item = await created(api)

    assert (await api.get(f"{ITEMS}/{item['key']}/events?limit=101")).status_code == 400
    assert (await api.get(f"{ITEMS}/{item['key']}/events?order=sideways")).status_code == 400
    assert (await api.get(f"{ITEMS}/{item['key']}/events?after_event_id=-1")).status_code == 400
