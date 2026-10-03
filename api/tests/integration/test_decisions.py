"""GET /decisions (SPEC 7): decisions with their reasons, newest first, through the visibility rule
(I3), with team and kind filters and a keyset cursor.
"""

import httpx

from app.config import Settings
from tests.integration.test_attention import world
from tests.support.items import created
from tests.support.seeded import SeededDatabase
from tests.support.workflow import ok, transition

DECISIONS = "/api/v1/decisions"


async def test_a_member_does_not_see_the_decision_on_a_confidential_item_a_lead_does(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        open_one = await created(c["rae"], team_key=w.key, title="Visible to the team")
        secret = await created(
            c["rae"], team_key=w.key, type="compliance_request", title="Confidential matter"
        )
        await ok(
            await transition(
                c["lena"], open_one, "close", reason="Not our problem", resolution="wont_do"
            )
        )
        await ok(
            await transition(
                c["lena"], secret, "close", reason="Handled with legal", resolution="wont_do"
            )
        )

        member = (await c["max"].get(f"{DECISIONS}?team={w.key}")).json()["items"]
        lead = (await c["lena"].get(f"{DECISIONS}?team={w.key}")).json()["items"]

    assert [d["item_key"] for d in member] == [open_one["key"]]
    assert [d["item_key"] for d in lead] == [secret["key"], open_one["key"]]  # newest first
    assert lead[0]["reason"] == "Handled with legal"
    assert lead[0]["actor"]["name"] == "Lena"
    assert lead[0]["team"]["key"] == w.key


async def test_decisions_filter_by_kind_and_page_with_a_cursor(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        first = await created(c["rae"], team_key=w.key, title="First to close")
        second = await created(c["rae"], team_key=w.key, title="Second to close")
        await ok(
            await transition(
                c["lena"], first, "close", reason="Duplicate work", resolution="wont_do"
            )
        )
        await ok(
            await transition(
                c["lena"], second, "close", reason="Out of scope", resolution="wont_do"
            )
        )

        page_one = (await c["lena"].get(f"{DECISIONS}?team={w.key}&limit=1")).json()
        page_two = (
            await c["lena"].get(
                f"{DECISIONS}?team={w.key}&limit=1&cursor={page_one['next_cursor']}"
            )
        ).json()
        closes = (await c["lena"].get(f"{DECISIONS}?team={w.key}&kind=closed")).json()["items"]
        approvals = (await c["lena"].get(f"{DECISIONS}?team={w.key}&kind=approval_approved")).json()
        other_team = (await c["lena"].get(f"{DECISIONS}?team=ZZZ")).json()

    assert [d["item_key"] for d in page_one["items"]] == [second["key"]]
    assert [d["item_key"] for d in page_two["items"]] == [first["key"]]
    assert page_two["next_cursor"] is None
    assert len(closes) == 2
    assert approvals["items"] == []
    assert other_team["items"] == []


async def test_decisions_need_a_session(api: httpx.AsyncClient) -> None:
    assert (await api.get(DECISIONS)).status_code == 401
