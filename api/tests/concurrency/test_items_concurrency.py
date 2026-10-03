"""CB2 and CB3 (SPEC 14): repeated and racing requests, with real concurrent connections."""

import asyncio
from typing import Any

import httpx
import pytest

from app.config import Settings
from app.domain.errors import Busy
from app.services import items as items_service
from tests.integration.conftest import csrf_headers
from tests.integration.test_teams_members import ASHA, MEERA, PRIYA, signed_in
from tests.support.items import (
    ITEMS,
    create_item,
    created,
    new_key,
    patch_item,
    rows,
    scalar,
    signed_in_as,
)
from tests.support.seeded import SeededDatabase

CONCURRENT = 10


# ----- T-IDEM -----------------------------------------------------------------------------


async def test_ten_concurrent_creates_with_one_key_make_exactly_one_item_and_ten_equal_answers(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    key = new_key()
    title = f"Concurrent create {key[:8]}"

    responses = await asyncio.gather(
        *(create_item(api, key=key, title=title) for _ in range(CONCURRENT))
    )

    assert [r.status_code for r in responses] == [201] * CONCURRENT
    assert all(r.json() == responses[0].json() for r in responses)
    replayed = [r for r in responses if r.headers.get("Idempotent-Replayed") == "true"]
    assert len(replayed) == CONCURRENT - 1
    assert await scalar(seeded.url, "SELECT count(*) FROM work_items WHERE title = $1", title) == 1
    created_events = await scalar(
        seeded.url,
        "SELECT count(*) FROM item_events e JOIN work_items w ON w.id = e.item_id "
        "WHERE w.title = $1 AND e.kind = 'created'",
        title,
    )
    assert created_events == 1
    assert (
        await scalar(seeded.url, "SELECT count(*) FROM idempotency_keys WHERE key = $1", key) == 1
    )


async def test_the_same_key_with_a_different_body_is_422_and_creates_nothing(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    key = new_key()
    first = await create_item(api, key=key, title="Original request title")

    second = await create_item(api, key=key, title="A different request title")

    assert first.status_code == 201
    assert second.status_code == 422
    assert second.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (
        await scalar(
            seeded.url,
            "SELECT count(*) FROM work_items WHERE title = $1",
            "A different request title",
        )
        == 0
    )


async def test_key_order_and_spacing_of_the_json_do_not_change_the_fingerprint(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)
    key = new_key()
    headers = {**csrf_headers(api), "Idempotency-Key": key, "Content-Type": "application/json"}
    one = '{"team_key":"PAY","type":"incident","title":"Same request twice"}'
    two = '{ "title": "Same request twice", "type": "incident", "team_key": "PAY" }'

    first = await api.post(ITEMS, content=one, headers=headers)
    second = await api.post(ITEMS, content=two, headers=headers)

    assert (first.status_code, second.status_code) == (201, 201)
    assert second.headers["Idempotent-Replayed"] == "true"
    assert second.json() == first.json()


async def test_one_users_key_does_not_collide_with_anothers(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    key = new_key()
    mine = await create_item(api, key=key, title="Same key, first user")
    async with signed_in_as(app_settings, ASHA) as other:
        theirs = await create_item(other, key=key, title="Same key, second user")

    assert (mine.status_code, theirs.status_code) == (201, 201)
    assert mine.json()["key"] != theirs.json()["key"]
    assert "Idempotent-Replayed" not in theirs.headers


async def test_a_domain_4xx_is_stored_and_replayed(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    key = new_key()

    first = await create_item(api, key=key, team_key="ZZZ")  # no such team
    retry = await create_item(api, key=key, team_key="ZZZ")

    assert (first.status_code, retry.status_code) == (400, 400)
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.headers["content-type"].startswith("application/problem+json")
    assert "Idempotent-Replayed" not in first.headers


async def test_a_5xx_rolls_back_the_item_and_the_key_so_the_retry_runs_again(
    api: httpx.AsyncClient, seeded: SeededDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signed_in(api, MEERA)
    key = new_key()
    title = f"Fails after writing {key[:8]}"
    real = items_service.create_item
    calls = 0

    async def writes_then_crashes(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        result = await real(*args, **kwargs)  # the item, its event and its watcher are written...
        if calls == 1:
            raise RuntimeError("boom after the write")  # ...and then the request dies
        return result

    monkeypatch.setattr(items_service, "create_item", writes_then_crashes)

    crashed = await create_item(api, key=key, title=title)
    assert crashed.status_code == 500
    assert crashed.json()["code"] == "INTERNAL"
    assert await scalar(seeded.url, "SELECT count(*) FROM work_items WHERE title = $1", title) == 0
    assert (
        await scalar(seeded.url, "SELECT count(*) FROM idempotency_keys WHERE key = $1", key) == 0
    )

    retry = await create_item(api, key=key, title=title)

    assert retry.status_code == 201
    assert "Idempotent-Replayed" not in retry.headers  # it ran again, it was not a replay
    assert await scalar(seeded.url, "SELECT count(*) FROM work_items WHERE title = $1", title) == 1


async def test_a_busy_503_is_not_stored_so_the_retry_with_the_same_key_succeeds(
    api: httpx.AsyncClient, seeded: SeededDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    await signed_in(api, MEERA)
    key = new_key()
    real = items_service.create_item
    calls = 0

    async def busy_once(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise Busy("try again", headers={"Retry-After": "1"})
        return await real(*args, **kwargs)

    monkeypatch.setattr(items_service, "create_item", busy_once)

    first = await create_item(api, key=key, title="Busy then fine")
    retry = await create_item(api, key=key, title="Busy then fine")

    assert first.status_code == 503
    assert first.headers["Retry-After"] == "1"
    assert retry.status_code == 201
    assert "Idempotent-Replayed" not in retry.headers
    assert (
        await scalar(seeded.url, "SELECT count(*) FROM idempotency_keys WHERE key = $1", key) == 1
    )


async def test_twenty_concurrent_creates_get_distinct_gap_free_numbers(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)

    responses = await asyncio.gather(
        *(create_item(api, team_key="FIN", title=f"Parallel request {i}") for i in range(20))
    )

    assert [r.status_code for r in responses] == [201] * 20
    numbers = sorted(int(r.json()["key"].split("-")[1]) for r in responses)
    assert numbers == list(range(numbers[0], numbers[0] + 20))
    seq = await scalar(seeded.url, "SELECT item_seq FROM teams WHERE key = 'FIN'")
    assert seq == numbers[-1]


# ----- T-STALE ----------------------------------------------------------------------------


async def test_two_edits_on_the_same_version_at_once_one_wins_the_other_gets_412(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    async with (
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, ASHA) as member,
    ):
        for round_ in range(6):  # a race is only a race if you run it a few times
            item = await created(api, priority=2, title=f"Race round {round_} for the title")

            by_lead, by_member = await asyncio.gather(
                patch_item(lead, item["key"], {"title": f"Lead rename {round_}"}, version=1),
                patch_item(member, item["key"], {"priority": 1}, version=1),
            )

            statuses = sorted([by_lead.status_code, by_member.status_code])
            assert statuses == [200, 412], (round_, statuses)
            winner, loser = (
                (by_lead, by_member) if by_lead.status_code == 200 else (by_member, by_lead)
            )
            final = await rows(
                seeded.url, "SELECT version FROM work_items WHERE key = $1", item["key"]
            )
            assert final[0]["version"] == 2  # old version + 1, never +2
            problem = loser.json()
            assert problem["code"] == "VERSION_CONFLICT"
            winner_kind = "field_changed" if winner is by_lead else "priority_changed"
            assert [(e["kind"], e["item_version"]) for e in problem["changes_since"]] == [
                (winner_kind, 2)
            ]
            assert problem["current"]["version"] == 2


async def test_many_edits_on_one_version_at_once_exactly_one_wins(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    responses = await asyncio.gather(
        *(
            patch_item(api, item["key"], {"title": f"Contender number {i}"}, version=1)
            for i in range(CONCURRENT)
        )
    )

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [412] * (CONCURRENT - 1)
    winner = next(r for r in responses if r.status_code == 200).json()
    assert winner["version"] == 2
    kinds = await rows(
        seeded.url,
        "SELECT e.kind, e.item_version FROM item_events e JOIN work_items w ON w.id = e.item_id "
        "WHERE w.key = $1 ORDER BY e.id",
        item["key"],
    )
    assert [(r["kind"], r["item_version"]) for r in kinds] == [("created", 1), ("field_changed", 2)]
    for loser in (r for r in responses if r.status_code == 412):
        assert len(loser.json()["changes_since"]) == 1


async def test_a_chain_of_concurrent_edits_each_using_the_latest_version_never_loses_one(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    version = item["version"]
    applied = 0

    for _ in range(8):
        responses = await asyncio.gather(
            patch_item(api, item["key"], {"title": f"Chain {applied} a title"}, version=version),
            patch_item(api, item["key"], {"title": f"Chain {applied} b title"}, version=version),
        )
        winners = [r for r in responses if r.status_code == 200]
        assert len(winners) == 1
        applied += 1
        version = winners[0].json()["version"]

    assert version == 1 + applied
    assert (
        await scalar(seeded.url, "SELECT version FROM work_items WHERE key = $1", item["key"])
        == version
    )
