"""GET /me/notifications and POST /me/notifications/read (SPEC 11). The worker that fills the table
arrives in phase 6, so these tests insert rows the way it will."""

import uuid

import httpx

from app.config import Settings
from tests.integration.conftest import csrf_headers
from tests.integration.test_teams_members import signed_in
from tests.support.items import created, new_key, rows, scalar, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.users import execute
from tests.support.workflow import ASHA, MEERA, act, ok

NOTIFICATIONS = "/api/v1/me/notifications"
MARK_READ = f"{NOTIFICATIONS}/read"


async def clear(seeded: SeededDatabase, *emails: str) -> None:
    """The seed already fans notifications out to its demo users; start from none."""
    for email in emails:
        await execute(
            seeded.url,
            "DELETE FROM notifications WHERE user_id = (SELECT id FROM users WHERE email = $1)",
            email,
        )


async def notify(
    seeded: SeededDatabase, email: str, item: dict[str, object], count: int
) -> list[int]:
    """`count` notifications for `email` about the item: its created event, then comments."""
    return [
        int(n["id"])
        for n in await rows(
            seeded.url,
            "INSERT INTO notifications (user_id, item_id, event_id, kind) "
            "SELECT u.id, $2, e.id, e.kind FROM users u, "
            "  (SELECT id, kind FROM item_events WHERE item_id = $2 ORDER BY id LIMIT $3) e "
            "WHERE u.email = $1 RETURNING id",
            email,
            uuid.UUID(str(item["id"])),
            count,
        )
    ]


async def with_comments(
    api: httpx.AsyncClient, app_settings: Settings, comments: int
) -> dict[str, object]:
    await signed_in(api, MEERA)
    item = await created(api)
    async with signed_in_as(app_settings, ASHA) as asha:
        for n in range(comments):
            await ok(
                await act(
                    asha, item["key"], "comments", {"body": f"c{n}"}, idempotency_key=new_key()
                ),
                201,
            )
    return item


async def test_notifications_are_newest_first_a_keyset_page_with_an_unread_filter(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    item = await with_comments(api, app_settings, comments=4)  # five events in all
    await clear(seeded, MEERA)
    ids = await notify(seeded, MEERA, item, count=5)
    await execute(seeded.url, "UPDATE notifications SET read_at = now() WHERE id = $1", ids[0])

    first = (await api.get(NOTIFICATIONS, params={"limit": 2})).json()
    second = (
        await api.get(NOTIFICATIONS, params={"limit": 2, "cursor": first["next_cursor"]})
    ).json()
    unread = (await api.get(NOTIFICATIONS, params={"unread": "true"})).json()

    assert [n["id"] for n in first["items"]] == sorted(ids, reverse=True)[:2]
    assert [n["id"] for n in second["items"]] == sorted(ids, reverse=True)[2:4]
    assert first["items"][0]["kind"] == "commented"
    assert first["items"][0]["actor"]["name"] == "Asha Rao"
    assert ids[0] not in [n["id"] for n in unread["items"]]
    assert unread["next_cursor"] is None


async def test_marking_read_only_touches_my_unread_rows_and_ignores_other_peoples_ids(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    item = await with_comments(api, app_settings, comments=2)
    await clear(seeded, MEERA, ASHA)
    mine = await notify(seeded, MEERA, item, count=3)
    asha_rows = await notify(seeded, ASHA, item, count=3)

    some = await api.post(
        MARK_READ, json={"ids": [mine[0], asha_rows[0]]}, headers=csrf_headers(api)
    )
    rest = await api.post(MARK_READ, json={"all": True}, headers=csrf_headers(api))
    nothing = await api.post(MARK_READ, json={"all": True}, headers=csrf_headers(api))

    assert some.json() == {"marked": 1}  # Asha's id was not mine to mark
    assert rest.json() == {"marked": 2}
    assert nothing.json() == {"marked": 0}
    assert (
        await scalar(
            seeded.url,
            "SELECT count(*) FROM notifications WHERE id = ANY($1) AND read_at IS NULL",
            asha_rows,
        )
        == 3
    )


async def test_opening_an_item_marks_only_that_items_notifications_read_for_me(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    opened = await with_comments(api, app_settings, comments=2)
    other = await created(api)
    await clear(seeded, MEERA, ASHA)
    mine = await notify(seeded, MEERA, opened, count=3)
    elsewhere = await notify(seeded, MEERA, other, count=1)
    asha_rows = await notify(seeded, ASHA, opened, count=3)

    seen = await api.post(f"/api/v1/items/{opened['key']}/read", headers=csrf_headers(api))

    assert seen.status_code == 200
    unread = await rows(
        seeded.url,
        "SELECT id FROM notifications WHERE id = ANY($1) AND read_at IS NULL",
        mine + elsewhere + asha_rows,
    )
    assert sorted(int(r["id"]) for r in unread) == sorted(elsewhere + asha_rows)


async def test_marking_read_repeats_with_the_same_idempotency_key_and_needs_ids_or_all(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    item = await with_comments(api, app_settings, comments=1)
    await clear(seeded, MEERA)
    ids = await notify(seeded, MEERA, item, count=2)
    headers = {**csrf_headers(api), "Idempotency-Key": new_key()}

    first = await api.post(MARK_READ, json={"ids": ids}, headers=headers)
    again = await api.post(MARK_READ, json={"ids": ids}, headers=headers)
    neither = await api.post(MARK_READ, json={}, headers=csrf_headers(api))
    both = await api.post(MARK_READ, json={"ids": ids, "all": True}, headers=csrf_headers(api))

    assert first.json() == again.json() == {"marked": 2}  # the replay does not say "0"
    assert again.headers["Idempotent-Replayed"] == "true"
    assert (neither.status_code, both.status_code) == (400, 400)


async def test_a_notification_about_an_item_the_person_cannot_see_is_not_listed(
    api: httpx.AsyncClient, app_settings: Settings, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    secret = await created(
        api, team_key="CMP", type="compliance_request", title="Hidden from Farah"
    )
    open_item = await created(api, team_key="CMP", type="incident", title="Visible to Farah")
    farah = "farah@baton.test"  # CMP member, not a lead
    await clear(seeded, farah)
    await notify(seeded, farah, secret, count=1)
    await notify(seeded, farah, open_item, count=1)

    async with signed_in_as(app_settings, farah) as client:
        listed = (await client.get(NOTIFICATIONS)).json()["items"]
        unread = (await client.get(NOTIFICATIONS, params={"unread": "true"})).json()["items"]

    assert [n["item_key"] for n in listed] == [open_item["key"]]
    assert [n["item_key"] for n in unread] == [open_item["key"]]


async def test_notifications_need_a_session(api: httpx.AsyncClient) -> None:
    assert (await api.get(NOTIFICATIONS)).status_code == 401
