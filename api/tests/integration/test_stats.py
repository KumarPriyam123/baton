"""GET /stats/teams (SPEC 7): the dashboard counts only what the caller may see (I3), and each
figure equals what the same person gets from the queue with the matching filter.
"""

from typing import Any

import httpx

from app.config import Settings
from tests.integration.test_attention import World, world
from tests.support.items import ITEMS, created
from tests.support.seeded import SeededDatabase
from tests.support.users import execute
from tests.support.workflow import claimed

STATS = "/api/v1/stats/teams"
OPEN = "&".join(f"status={s}" for s in ("new", "in_progress", "blocked", "awaiting_approval"))


async def my_team(client: httpx.AsyncClient, w: World) -> dict[str, Any]:
    response = await client.get(STATS)
    assert response.status_code == 200, response.text
    return next(t for t in response.json()["teams"] if t["team"]["key"] == w.key)


async def listed(client: httpx.AsyncClient, w: World, query: str) -> int:
    page = (await client.get(f"{ITEMS}?team={w.key}&{query}&limit=100")).json()
    assert page["next_cursor"] is None
    return len(page["items"])


async def test_a_member_does_not_count_a_confidential_item_a_lead_does_and_both_match_their_list(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        await created(c["rae"], team_key=w.key, priority=0, title="Open and unowned")
        mine = await created(c["rae"], team_key=w.key, priority=2, title="Mia has this")
        await claimed(c["mia"], mine)
        secret = await created(
            c["rae"], team_key=w.key, type="compliance_request", title="Confidential, unowned"
        )
        assert secret["confidential"] is True
        await execute(
            seeded.url,
            "UPDATE work_items SET due_at = now() - interval '1 hour' WHERE key = $1",
            secret["key"],
        )

        member = await my_team(c["max"], w)
        lead = await my_team(c["lena"], w)
        member_list = (
            await listed(c["max"], w, OPEN),
            await listed(c["max"], w, "assignee=none"),
            await listed(c["max"], w, "overdue=true"),
        )
        lead_list = (
            await listed(c["lena"], w, OPEN),
            await listed(c["lena"], w, "assignee=none"),
            await listed(c["lena"], w, "overdue=true"),
        )

    assert (member["open"], member["unowned"], member["overdue"]) == (2, 1, 0)
    assert (lead["open"], lead["unowned"], lead["overdue"]) == (3, 2, 1)
    assert lead["by_priority"]["p0"] == 1
    assert member["by_priority"]["p0"] == 1
    assert lead["by_status"] == {"new": 2, "in_progress": 1, "blocked": 0, "awaiting_approval": 0}
    assert member_list == (2, 1, 0)  # the same numbers the queue gives the member
    assert lead_list == (3, 2, 1)
    assert secret["key"] not in [o["key"] for o in member["oldest"]]
    assert secret["key"] in [o["key"] for o in lead["oldest"]]
    assert [o["user"]["name"] for o in member["owners"]] == ["Mia"]


async def test_org_figures_are_the_sum_of_the_team_rows_and_a_stranger_sees_no_team(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        await created(w.clients["rae"], team_key=w.key, title="Rae's own request")

        mine = (await w.clients["rae"].get(STATS)).json()
        lead = (await w.clients["lena"].get(STATS)).json()

    assert [t["team"]["key"] for t in mine["teams"]] == [w.key]  # only where she can see items
    assert mine["org"]["open"] == sum(t["open"] for t in mine["teams"]) == 1
    assert lead["org"]["unowned"] == sum(t["unowned"] for t in lead["teams"])


async def test_stats_need_a_session(api: httpx.AsyncClient) -> None:
    assert (await api.get(STATS)).status_code == 401
