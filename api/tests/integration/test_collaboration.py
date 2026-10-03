"""Comments, watch and read (SPEC 6.2, 11) through the real app: one test per rule a reviewer
would probe, and each new read or write path is checked for visibility (I3) and idempotency (I6)."""

import httpx

from app.config import Settings
from tests.integration.conftest import csrf_headers
from tests.integration.test_teams_members import signed_in
from tests.support.items import ITEMS, created, new_key, rows, scalar, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.workflow import ASHA, DEV_VIEWER, ISHAAN, MEERA, act, ok

FARAH = "farah@baton.test"  # member of CMP, not a lead


async def comment(
    client: httpx.AsyncClient, item_key: str, body: str, *, key: str | None = None
) -> httpx.Response:
    return await act(client, item_key, "comments", {"body": body}, idempotency_key=key or new_key())


async def confidential_item(api: httpx.AsyncClient) -> dict[str, object]:
    await signed_in(api, MEERA)
    item = await created(api, team_key="CMP", type="compliance_request", title="Data export check")
    assert item["confidential"] is True
    return item


async def comment_count(url: str, item_key: str) -> int:
    count = await scalar(
        url,
        "SELECT count(*) FROM comments c JOIN work_items w ON w.id = c.item_id WHERE w.key = $1",
        item_key,
    )
    return int(count)


# ----- comments ---------------------------------------------------------------------------------


async def test_a_comment_writes_one_commented_event_without_bumping_the_version_and_watches(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    async with signed_in_as(app_settings, ASHA) as asha:
        result = await ok(await comment(asha, item["key"], "  Looking into it.  "), 201)

    assert result["comment"]["body"] == "Looking into it."
    assert result["comment"]["author"]["name"] == "Asha Rao"
    assert result["item"]["version"] == item["version"]  # decision 8: no version of its own
    assert result["item"]["last_event_id"] == result["comment"]["event_id"]
    assert result["item"]["watching"] is True
    events = await rows(
        seeded.url,
        "SELECT kind, item_version, data::text AS data FROM item_events e "
        "JOIN work_items w ON w.id = e.item_id WHERE w.key = $1 AND e.kind = 'commented'",
        item["key"],
    )
    assert [(e["kind"], e["item_version"]) for e in events] == [("commented", item["version"])]
    assert str(result["comment"]["id"]) in events[0]["data"]


async def test_repeating_a_comment_with_the_same_idempotency_key_stores_it_once(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    key = new_key()

    first = await comment(api, item["key"], "Double click", key=key)
    second = await comment(api, item["key"], "Double click", key=key)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json()["comment"]["id"] == first.json()["comment"]["id"]
    assert await comment_count(seeded.url, item["key"]) == 1


async def test_a_comment_needs_an_idempotency_key_and_a_body(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    no_key = await api.post(
        f"{ITEMS}/{item['key']}/comments", json={"body": "hi"}, headers=csrf_headers(api)
    )
    blank = await comment(api, item["key"], "   ")

    assert no_key.status_code == 400
    assert blank.status_code == 400


async def test_a_hidden_confidential_item_cannot_be_commented_on_or_read_or_watched(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    item = await confidential_item(api)
    key = str(item["key"])

    async with signed_in_as(app_settings, FARAH) as farah:  # CMP member, not a lead
        commented = await comment(farah, key, "Can you see this?")
        read = await act(farah, key, "read")
        watched = await farah.put(f"{ITEMS}/{key}/watch", headers=csrf_headers(farah))
        timeline = await farah.get(f"{ITEMS}/{key}/events")
    async with signed_in_as(app_settings, ISHAAN) as ishaan:  # CMP lead
        allowed = await comment(ishaan, key, "Lead can.")

    assert [r.status_code for r in (commented, read, watched, timeline)] == [404] * 4
    assert allowed.status_code == 201
    assert await comment_count(seeded.url, key) == 1  # only the lead's


async def test_a_viewer_can_comment_but_comments_cannot_be_edited_or_deleted(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    async with signed_in_as(app_settings, DEV_VIEWER) as viewer:  # SPEC 5.2: viewers comment
        made = await ok(await comment(viewer, item["key"], "A viewer's note"), 201)
        url = f"{ITEMS}/{item['key']}/comments/{made['comment']['id']}"

        statuses = []
        for method in ("PUT", "PATCH", "DELETE"):
            response = await viewer.request(
                method, url, json={"body": "x"}, headers=csrf_headers(viewer)
            )
            statuses.append(response.status_code)

    assert statuses == [404, 404, 404]  # there is no such route (I9)


async def test_the_timeline_carries_the_comment_text_on_its_commented_event(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    await ok(await comment(api, item["key"], "First **note**"), 201)

    events = (await api.get(f"{ITEMS}/{item['key']}/events")).json()["items"]

    assert [(e["kind"], e["comment_body"]) for e in events] == [
        ("created", None),
        ("commented", "First **note**"),
    ]


# ----- read marker ------------------------------------------------------------------------------


async def test_unread_means_events_after_the_marker_and_reading_clears_it(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    assert (await ok(await act(api, item["key"], "read")))["unread_since_event_id"] is None
    marker = item["last_event_id"]
    async with signed_in_as(app_settings, ASHA) as asha:
        await ok(await comment(asha, item["key"], "Update for you"), 201)

    behind = (await api.get(f"{ITEMS}/{item['key']}")).json()
    cleared = await ok(await act(api, item["key"], "read"))

    assert behind["unread_since_event_id"] == marker  # a comment counts, though it has no version
    assert cleared["unread_since_event_id"] is None
    assert cleared["last_event_id"] == behind["last_event_id"]


# ----- watch ------------------------------------------------------------------------------------


async def test_watch_and_unwatch_are_repeatable_and_never_touch_the_version(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    async with signed_in_as(app_settings, ASHA) as asha:
        headers = csrf_headers(asha)
        before = (await asha.get(f"{ITEMS}/{item['key']}")).json()
        on = [await asha.put(f"{ITEMS}/{item['key']}/watch", headers=headers) for _ in range(2)]
        off = [await asha.delete(f"{ITEMS}/{item['key']}/watch", headers=headers) for _ in range(2)]

    assert before["watching"] is False
    assert [r.json()["watching"] for r in on] == [True, True]
    assert [r.json()["watching"] for r in off] == [False, False]
    assert {r.json()["version"] for r in (*on, *off)} == {item["version"]}
