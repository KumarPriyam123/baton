"""CB1 and CB5 (SPEC 14): many people act on one item at the same moment.

Each racer has a connection of its own and is released together with an asyncio.Barrier, so the
requests really overlap on the row lock instead of running one after another.
"""

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings
from app.db.tx import run_command
from app.domain.hashing import subject_hash
from app.repo.users import load_actor_context
from app.services import commands
from tests.integration.conftest import csrf_headers
from tests.integration.test_teams_members import new_user, signed_in
from tests.support.items import created, patch_item, rows, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.users import PASSWORD
from tests.support.workflow import (
    ASHA,
    MEERA,
    PRIYA,
    VIKRAM,
    act,
    claimed,
    decide,
    events,
    extra_workers,
    get,
    many_clients,
    requested_approval,
    row,
    team_id,
)

CLAIMANTS = 20


Racer = Callable[[], Awaitable[Any]]


async def released_together(*racers: Racer) -> list[Any]:
    """Run every racer as its own task; they all wait at a barrier and then go at once."""
    barrier = asyncio.Barrier(len(racers))

    async def go(racer: Racer) -> Any:
        await barrier.wait()
        return await racer()

    return list(await asyncio.gather(*(go(r) for r in racers)))


# ----- T-CLAIM ----------------------------------------------------------------------------


async def test_claim_twenty_concurrent_requests_exactly_one_wins(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    """The service level: 20 people, 20 connections of their own, released together."""
    await signed_in(api, MEERA)
    item = await created(api)
    emails = await extra_workers(seeded.url, "PAY", CLAIMANTS)
    ids = [
        r["id"]
        for r in await rows(seeded.url, "SELECT id FROM users WHERE email = ANY($1)", emails)
    ]
    engine = create_async_engine(app_settings.database_url, pool_size=CLAIMANTS)
    try:
        connections = [await engine.connect() for _ in ids]
        contexts = [
            await load_actor_context(c, user_id)
            for c, user_id in zip(connections, ids, strict=True)
        ]

        async def claim(n: int) -> str:
            ctx = contexts[n]
            assert ctx is not None
            try:
                await run_command(
                    connections[n],
                    lambda tx: commands.claim(tx, ctx, item["key"]),
                    actor_id=ids[n],
                )
            except commands.ClaimLost:
                return "lost"
            return "won"

        outcomes = await released_together(*(partial(claim, n) for n in range(CLAIMANTS)))
        for connection in connections:
            await connection.close()
    finally:
        await engine.dispose()

    assert outcomes.count("won") == 1
    assert outcomes.count("lost") == CLAIMANTS - 1
    stored = await row(seeded.url, item["key"])
    assert stored["version"] == item["version"] + 1
    assert stored["status"] == "in_progress"
    assigned = [e for e in await events(seeded.url, item["key"]) if e["kind"] == "assigned"]
    assert len(assigned) == 1
    assert assigned[0]["actor_id"] == stored["assignee_id"]


async def test_claim_twenty_concurrent_http_requests_one_200_and_nineteen_409(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    """The same race through the real endpoint, 20 signed-in browsers."""
    await signed_in(api, MEERA)
    item = await created(api)
    emails = await extra_workers(seeded.url, "PAY", CLAIMANTS)

    async with many_clients(app_settings, emails, PASSWORD) as clients:
        responses = await released_together(
            *(partial(act, client, item["key"], "claim") for client in clients)
        )

    codes = sorted(r.status_code for r in responses)
    assert codes == [200] + [409] * (CLAIMANTS - 1)
    winner = next(r for r in responses if r.status_code == 200).json()
    for lost in (r for r in responses if r.status_code == 409):
        body = lost.json()
        assert body["code"] == "ALREADY_CLAIMED"
        assert body["claimed_by"]["id"] == winner["assignee"]["id"]
        assert body["claimed_at"]
    stored = await row(seeded.url, item["key"])
    assert (stored["version"], str(stored["assignee_id"])) == (
        item["version"] + 1,
        winner["assignee"]["id"],
    )
    assigned = [e for e in await events(seeded.url, item["key"]) if e["kind"] == "assigned"]
    assert len(assigned) == 1


async def test_a_claim_racing_a_lead_assigning_has_one_owner_and_a_clear_loser(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        rahul = (await lead.get("/api/v1/users", params={"q": "rahul"})).json()["items"][0]
        for round_ in range(8):
            item = await created(api, title=f"Claim against assign {round_}")
            by_asha, by_lead = await released_together(
                partial(act, asha, item["key"], "claim"),
                partial(
                    act,
                    lead,
                    item["key"],
                    "assign",
                    {"assignee_id": rahul["id"]},
                    version=item["version"],
                ),
            )

            stored = await row(seeded.url, item["key"])
            assert stored["version"] == item["version"] + 1, round_
            assert (by_asha.status_code, by_lead.status_code) in {(200, 412), (409, 200)}, round_
            if by_asha.status_code == 200:
                assert str(stored["assignee_id"]) == by_asha.json()["assignee"]["id"]
            else:
                assert by_asha.json()["code"] == "ALREADY_CLAIMED"
                assert str(stored["assignee_id"]) == rahul["id"]


# ----- the approval race ------------------------------------------------------------------


async def payment_item(api: httpx.AsyncClient, asha: httpx.AsyncClient, n: int) -> dict[str, Any]:
    item = await created(
        api, type="payment_investigation", title=f"Refund round {n}", description="Original text"
    )
    return await requested_approval(asha, await claimed(asha, item))


async def covered(seeded: SeededDatabase, key: str) -> list[tuple[str, bool]]:
    """Each approved approval of the item, and whether it covers the content the item has now."""
    item = await row(seeded.url, key)
    now = subject_hash(item["team_id"], item["type"], item["title"], item["description"])
    found = await rows(
        seeded.url,
        "SELECT status::text AS status, subject_hash FROM approvals WHERE item_id = $1",
        item["id"],
    )
    return [(r["status"], r["subject_hash"] == now) for r in found if r["status"] == "approved"]


async def test_approve_racing_an_edit_never_leaves_an_approval_on_content_nobody_reviewed(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    """T-APPROVE-RACE: 50 rounds. A lead approves what they reviewed (If-Match v) while the owner
    edits the same item (also If-Match v). Half the rounds edit the description (material), half
    the priority (not). Exactly one of them wins the version; the loser re-reads and tries again,
    the way the web client does after a 412."""
    await signed_in(api, MEERA)
    async with signed_in_as(app_settings, PRIYA) as lead, signed_in_as(app_settings, ASHA) as asha:
        for round_ in range(50):
            material = round_ % 2 == 0
            waiting = await payment_item(api, asha, round_)
            change = (
                {"description": f"Edited while under review {round_}"}
                if material
                else {"priority": 0}
            )

            approve, edit = await released_together(
                partial(decide, lead, waiting, "approve"),
                partial(patch_item, asha, waiting["key"], change, version=waiting["version"]),
            )

            where = (round_, material, approve.status_code, edit.status_code)
            assert (approve.status_code, edit.status_code) in {(200, 412), (412, 200)}, where
            key = waiting["key"]
            if approve.status_code == 200:
                # Approved first: the approval is on the content the lead reviewed...
                assert await covered(seeded, key) == [("approved", True)], where
                # ...and the owner's stale edit is refused until it looks again.
                current = await get(asha, key)
                retried = await patch_item(asha, key, change, version=current["version"])
                assert retried.status_code == 200, where
                expected = [] if material else [("approved", True)]
                assert await covered(seeded, key) == expected, where
            else:
                # The edit won: the lead's view was stale. 412, nothing approved, and a material
                # edit already ended the request they were looking at.
                assert approve.json()["code"] == "VERSION_CONFLICT", where
                assert await covered(seeded, key) == [], where

            history = await events(seeded.url, key)
            approved = [e for e in history if e["kind"] == "approval_approved"]
            assert len(approved) <= 1, where
            for e in approved:
                # Nothing happened between the version the lead reviewed and their approval.
                assert e["item_version"] == waiting["version"] + 1, where
            versions = sorted({e["item_version"] for e in history})
            assert versions == list(range(1, versions[-1] + 1)), where


async def test_two_requests_for_approval_at_once_one_is_201_and_one_is_409(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        for round_ in range(10):
            item = await claimed(
                asha, await created(api, type="payment_investigation", title=f"Ask twice {round_}")
            )

            first, second = await released_together(
                partial(act, asha, item["key"], "approvals", {}, version=item["version"]),
                partial(act, lead, item["key"], "approvals", {}, version=item["version"]),
            )

            assert sorted([first.status_code, second.status_code]) == [201, 409], round_
            loser = first if first.status_code == 409 else second
            assert loser.json()["code"] == "APPROVAL_ALREADY_PENDING", round_
            pending = await rows(
                seeded.url,
                "SELECT count(*) AS n FROM approvals a JOIN work_items w ON w.id = a.item_id "
                "WHERE w.key = $1 AND a.status = 'pending'",
                item["key"],
            )
            assert pending[0]["n"] == 1, round_
            requested = [
                e
                for e in await events(seeded.url, item["key"])
                if e["kind"] == "approval_requested"
            ]
            assert len(requested) == 1, round_


async def test_ten_requests_to_approve_one_request_approve_it_once(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await payment_item(api, asha, 0)

        responses = await released_together(
            *(partial(decide, lead, waiting, "approve") for _ in range(10))
        )

    assert sorted(r.status_code for r in responses) == [200] + [412] * 9
    approved = [
        e for e in await events(seeded.url, waiting["key"]) if e["kind"] == "approval_approved"
    ]
    assert len(approved) == 1


async def test_a_lead_cancelling_while_another_approves_has_one_outcome(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    async with (
        signed_in_as(app_settings, ASHA) as asha,
        signed_in_as(app_settings, PRIYA) as lead,
        signed_in_as(app_settings, VIKRAM) as other,
    ):
        for round_ in range(10):
            waiting = await payment_item(api, asha, round_)
            approve, cancel = await released_together(
                partial(decide, lead, waiting, "approve"),
                partial(
                    act, other, waiting["key"], f"approvals/{waiting['approval']['id']}/cancel"
                ),
            )

            assert (approve.status_code, cancel.status_code) in {(200, 409), (412, 200)}, round_
            final = await get(asha, waiting["key"])
            assert final["approval"]["status"] in {"approved", "cancelled"}, round_
            assert final["status"] == "in_progress", round_
            kinds = [e["kind"] for e in await events(seeded.url, waiting["key"])]
            assert kinds.count("approval_approved") + kinds.count("approval_cancelled") == 1, round_


# ----- 4.3b against an assignment ---------------------------------------------------------


async def test_assigning_someone_while_they_are_being_removed_never_leaves_them_owning_it(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    pay = await team_id(seeded.url, "PAY")
    await signed_in(api, MEERA)
    async with (
        signed_in_as(app_settings, PRIYA) as assigner,
        signed_in_as(app_settings, VIKRAM) as remover,
    ):
        for round_ in range(15):
            _, worker = await new_user(seeded, memberships={pay: "member"})
            item = await created(api, title=f"Assign against removal {round_}")

            assigned, removed = await released_together(
                partial(
                    act,
                    assigner,
                    item["key"],
                    "assign",
                    {"assignee_id": str(worker)},
                    version=item["version"],
                ),
                partial(
                    remover.delete,
                    f"/api/v1/teams/PAY/members/{worker}",
                    headers=csrf_headers(remover),
                ),
            )

            assert removed.status_code == 204, (round_, removed.text)
            assert assigned.status_code in {200, 409}, (round_, assigned.text)
            stored = await row(seeded.url, item["key"])
            still_a_member = await rows(
                seeded.url,
                "SELECT 1 FROM memberships WHERE team_id = $1 AND user_id = $2",
                pay,
                worker,
            )
            assert not still_a_member
            assert stored["assignee_id"] != worker, (round_, assigned.status_code)
            assert stored["status"] in {"new", "in_progress"}
            if stored["assignee_id"] is None:
                assert stored["status"] == "new"


async def test_claims_by_a_member_being_removed_leave_no_item_with_them(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    pay = await team_id(seeded.url, "PAY")
    await signed_in(api, MEERA)
    async with signed_in_as(app_settings, VIKRAM) as remover:
        for round_ in range(8):
            email, worker = await new_user(seeded, memberships={pay: "member"})
            item = await created(api, title=f"Claim against removal {round_}")
            async with signed_in_as_password(app_settings, email) as member:
                claimed_, removed = await released_together(
                    partial(act, member, item["key"], "claim"),
                    partial(
                        remover.delete,
                        f"/api/v1/teams/PAY/members/{worker}",
                        headers=csrf_headers(remover),
                    ),
                )

            assert removed.status_code == 204, (round_, removed.text)
            stored = await row(seeded.url, item["key"])
            assert stored["assignee_id"] != worker, (round_, claimed_.status_code)
            assert claimed_.status_code in {200, 403, 404}, (round_, claimed_.text)


def signed_in_as_password(settings: Settings, email: str) -> Any:
    from contextlib import asynccontextmanager

    from tests.integration.conftest import sign_in
    from tests.support.app import running_app

    @asynccontextmanager
    async def manager() -> Any:
        async with running_app(settings) as client:
            assert (await sign_in(client, email, PASSWORD)).status_code == 200
            yield client

    return manager()
