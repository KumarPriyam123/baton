"""Search (`q` on GET /items) and /items/similar (SPEC 8) through the real app."""

import httpx

from app.config import Settings
from tests.integration.test_teams_members import signed_in
from tests.support.items import ITEMS, created, signed_in_as
from tests.support.workflow import ADMIN, ISHAAN, MEERA

FARAH = "farah@baton.test"  # member of CMP, not a lead


def keys(page: dict[str, object]) -> list[str]:
    items = page["items"]
    assert isinstance(items, list)
    return [i["key"] for i in items]


async def team_id(client: httpx.AsyncClient, key: str) -> str:
    teams = (await client.get("/api/v1/teams")).json()
    return str(next(t["id"] for t in teams if t["key"] == key))


async def test_a_key_shaped_query_jumps_to_that_item_first(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api, title="Ledger drift in the nightly batch")
    await created(api, title=f"Mentions {item['key']} in passing", description=item["key"])

    page = (await api.get(f"{ITEMS}", params={"q": item["key"].lower()})).json()

    assert keys(page)[0] == item["key"]
    assert page["next_cursor"] is None


async def test_a_title_match_ranks_above_a_description_only_match(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)
    in_description = await created(
        api, title="Quarterly cleanup", description="The job also touches the marmalade table."
    )
    in_title = await created(api, title="Marmalade sync is failing", description="No detail yet.")

    page = (await api.get(ITEMS, params={"q": "marmalade"})).json()

    assert keys(page) == [in_title["key"], in_description["key"]]


async def test_a_typo_in_the_title_still_finds_the_item_by_trigram(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api, title="Reconciliation report is empty", description="")

    page = (await api.get(ITEMS, params={"q": "reconcilation"})).json()  # the "i" is missing

    assert item["key"] in keys(page)


async def test_search_is_ranked_not_paged_and_does_not_take_a_sort(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)

    with_cursor = await api.get(ITEMS, params={"q": "refund", "cursor": "abc"})
    with_sort = await api.get(ITEMS, params={"q": "refund", "sort": "updated"})
    blank = await api.get(ITEMS, params={"q": "   "})
    top = (await api.get(ITEMS, params={"q": "refund", "limit": 100})).json()

    assert [r.status_code for r in (with_cursor, with_sort, blank)] == [400, 400, 400]
    assert len(top["items"]) <= 50  # the SPEC's top 50, whatever limit was asked for


async def test_search_and_facets_never_return_or_count_a_confidential_item_the_caller_cannot_see(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)  # a Support member raises a compliance request
    item = await created(
        api, team_key="CMP", type="compliance_request", title="Zebrafish export check"
    )

    async def look(email: str) -> tuple[list[str], dict[str, int], list[str]]:
        async with signed_in_as(app_settings, email) as client:
            found = keys((await client.get(ITEMS, params={"q": "zebrafish"})).json())
            facets = (await client.get(f"{ITEMS}/facets", params={"q": "zebrafish"})).json()
            similar = (
                await client.get(
                    f"{ITEMS}/similar",
                    params={"team_id": await team_id(client, "CMP"), "title": "Zebrafish export"},
                )
            ).json()
            return found, facets["status"], [s["key"] for s in similar]

    member = await look(FARAH)  # CMP member, not a lead
    lead = await look(ISHAAN)
    admin = await look(ADMIN)

    assert member == ([], {}, [])
    assert lead == ([item["key"]], {"new": 1}, [item["key"]])
    assert admin[0] == [item["key"]]
    assert keys((await api.get(ITEMS, params={"q": "zebrafish"})).json()) == [item["key"]]


async def test_similar_suggests_open_items_of_that_team_only(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    pay = await created(api, title="Wire transfer reconciliation mismatch")
    sre = await created(api, team_key="SRE", title="Wire transfer reconciliation mismatch")

    found = (
        await api.get(
            f"{ITEMS}/similar",
            params={
                "team_id": await team_id(api, "PAY"),
                "title": "Wire transfer reconciliation mismatch Q3",
            },
        )
    ).json()

    assert pay["key"] in [s["key"] for s in found]
    assert sre["key"] not in [s["key"] for s in found]
    assert all(s["score"] > 0.35 for s in found)
    assert len(found) <= 5
