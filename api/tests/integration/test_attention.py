"""GET /me/attention (SPEC 7): each section's rule with an item that belongs in it and one that
does not, counts capped at 100, and the visibility rule inside every section (I3).

Every test builds its own team, so the seeded demo data cannot appear in a section.
"""

import random
import string
import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import Settings
from app.demo import DEMO_PASSWORD
from tests.support.items import created, scalar, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.users import create_user, execute
from tests.support.workflow import act, claimed, ok, requested_approval

ATTENTION = "/api/v1/me/attention"


@dataclass
class World:
    """A team of its own: two leads, two members, a viewer and an outside requester."""

    key: str
    team_id: uuid.UUID
    clients: dict[str, httpx.AsyncClient]


@asynccontextmanager
async def world(seeded: SeededDatabase, settings: Settings) -> AsyncIterator[World]:
    key = "".join(random.choices(string.ascii_uppercase, k=4))
    team_id = await scalar(
        seeded.url, "INSERT INTO teams (key, name) VALUES ($1, $2) RETURNING id", key, f"Team {key}"
    )
    people = {
        "lena": "lead",
        "leo": "lead",
        "mia": "member",
        "max": "member",
        "vic": "viewer",
        "rae": None,  # not in the team: only raises requests
    }
    async with AsyncExitStack() as stack:
        clients: dict[str, httpx.AsyncClient] = {}
        for name, role in people.items():
            await create_user(
                seeded.url,
                f"{name}.{key.lower()}@baton.test",
                password=DEMO_PASSWORD,
                name=name.title(),
                memberships={team_id: role} if role else None,
            )
            clients[name] = await stack.enter_async_context(
                signed_in_as(settings, f"{name}.{key.lower()}@baton.test")
            )
        yield World(key, team_id, clients)


async def section(client: httpx.AsyncClient, name: str) -> dict[str, Any]:
    page = (await client.get(ATTENTION)).json()
    found: dict[str, Any] = next(s for s in page["sections"] if s["key"] == name)
    return found


async def keys_in(client: httpx.AsyncClient, name: str) -> list[str]:
    return [i["key"] for i in (await section(client, name))["items"]]


async def keys_for(w: World, names: tuple[str, ...], name: str) -> list[list[str]]:
    """The keys in one section, as seen by each of the named people."""
    return [await keys_in(w.clients[n], name) for n in names]


async def test_the_five_sections_come_in_the_order_of_the_spec(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        page = (await w.clients["mia"].get(ATTENTION)).json()

    assert [s["key"] for s in page["sections"]] == [
        "approvals",
        "urgent",
        "unowned",
        "quiet",
        "requests",
    ]


async def test_needs_your_approval_is_for_leads_and_never_your_own_request(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        theirs = await created(c["rae"], team_key=w.key, type="incident", title="Theirs to approve")
        await requested_approval(c["mia"], await claimed(c["mia"], theirs))
        own = await created(c["rae"], team_key=w.key, type="incident", title="Lena asks herself")
        await requested_approval(c["lena"], await claimed(c["lena"], own))

        lena, leo, mia = await keys_for(w, ("lena", "leo", "mia"), "approvals")

    assert (theirs["key"] in lena, own["key"] in lena) == (True, False)  # not her own request
    assert {theirs["key"], own["key"]} <= set(leo)  # the other lead can decide both
    assert mia == []  # a member cannot approve, so it is not on their list


async def test_yours_overdue_or_urgent_means_assigned_open_and_sla_breached_or_p0_p1(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        urgent = await created(c["rae"], team_key=w.key, priority=1, title="P1 for Mia")
        late = await created(c["rae"], team_key=w.key, priority=3, title="Late P3 for Mia")
        calm = await created(c["rae"], team_key=w.key, priority=3, title="Calm P3 for Mia")
        for item in (urgent, late, calm):
            await claimed(c["mia"], item)
        await execute(
            seeded.url,
            "UPDATE work_items SET sla_breached_at = now() WHERE key = $1",
            late["key"],
        )

        mine = await keys_in(c["mia"], "urgent")
        others = await keys_in(c["max"], "urgent")

    assert set(mine) == {urgent["key"], late["key"]}  # not the calm P3
    assert mine[0] == urgent["key"]  # priority first
    assert others == []  # only the assignee's own


async def test_needs_an_owner_is_for_members_and_leads_and_skips_what_they_cannot_see(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        free = await created(c["rae"], team_key=w.key, title="Nobody has this")
        taken = await created(c["rae"], team_key=w.key, title="Mia has this")
        await claimed(c["mia"], taken)
        secret = await created(
            c["rae"], team_key=w.key, type="compliance_request", title="Confidential and unowned"
        )
        assert secret["confidential"] is True

        member, lead, viewer = await keys_for(w, ("max", "lena", "vic"), "unowned")

    assert member == [free["key"]]  # not the claimed one; not the confidential one (I3)
    assert set(lead) == {free["key"], secret["key"]}  # a lead sees confidential items
    assert viewer == []  # a viewer cannot take work


async def test_going_quiet_is_active_assigned_to_you_and_idle_for_72_hours(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        quiet = await created(c["rae"], team_key=w.key, title="Silent for three days")
        recent = await created(c["rae"], team_key=w.key, title="Silent for two days")
        for item in (quiet, recent):
            await claimed(c["mia"], item)
        for item, hours in ((quiet, 73), (recent, 71)):
            await execute(
                seeded.url,
                "UPDATE work_items SET last_activity_at = now() - make_interval(hours => $2) "
                "WHERE key = $1",
                item["key"],
                hours,
            )

        mine = await keys_in(c["mia"], "quiet")
        others = await keys_in(c["max"], "quiet")

    assert mine == [quiet["key"]]
    assert others == []


async def test_your_requests_shows_activity_by_someone_else_until_you_open_it(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        c = w.clients
        item = await created(c["rae"], team_key=w.key, title="Rae asked for this")
        before = await keys_in(c["rae"], "requests")  # only her own `created` event so far
        await claimed(c["mia"], item)
        after_claim = await keys_in(c["rae"], "requests")
        await ok(await act(c["rae"], item["key"], "read"))
        after_read = await keys_in(c["rae"], "requests")
        mine_only = await keys_in(c["mia"], "requests")

    assert (before, after_claim, after_read) == ([], [item["key"]], [])
    assert mine_only == []  # Mia raised nothing


async def test_counts_stop_at_100_and_only_ten_items_are_listed(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    async with world(seeded, app_settings) as w:
        await execute(
            seeded.url,
            "INSERT INTO work_items (key, team_id, number, origin_team_id, type, title, priority, "
            "requester_id, confidential, requires_approval) "
            "SELECT $1 || '-' || n, $2, n, $2, 'incident', 'Backlog item ' || n, 2, "
            "(SELECT id FROM users WHERE email = $3), false, false "
            "FROM generate_series(1, 130) AS n",
            w.key,
            w.team_id,
            f"rae.{w.key.lower()}@baton.test",
        )

        unowned = await section(w.clients["mia"], "unowned")

    assert unowned["count"] == 100  # the UI shows "100+"
    assert len(unowned["items"]) == 10


async def test_attention_needs_a_session(api: httpx.AsyncClient) -> None:
    assert (await api.get(ATTENTION)).status_code == 401
