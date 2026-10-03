"""Edges of the workflow commands found by the phase 4 review: confidential items, hidden items,
a lock that is held while a transfer commits, and idempotent replays (SPEC 5.1, 6.3, I3, I6)."""

import asyncio
import uuid
from typing import Any

import asyncpg
import httpx

from app.config import Settings
from app.db.urls import asyncpg_dsn
from tests.integration.test_teams_members import signed_in
from tests.support.items import ITEMS, created, new_key, patch_item, signed_in_as
from tests.support.seeded import SeededDatabase
from tests.support.workflow import (
    ASHA,
    MEERA,
    PRIYA,
    VIKRAM,
    act,
    claimed,
    decide,
    events,
    get,
    ok,
    requested_approval,
    row,
    transition,
)

SAMEER = "sameer@baton.test"  # Finance member, Compliance viewer: nothing to do with PAY
FARAH = "farah@baton.test"  # Compliance member: sees confidential items only if she owns them
ISHAAN = "ishaan.lead@baton.test"  # Compliance lead


async def new_item(api: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    await signed_in(api, MEERA)
    return await created(api, **overrides)


# ----- a command that takes away the actor's own right to see the item -------------------------


async def test_a_member_can_release_an_item_that_became_confidential_and_then_loses_sight_of_it(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        owned = await claimed(asha, item)
        hidden = await ok(
            await patch_item(lead, item["key"], {"confidential": True}, version=owned["version"])
        )
        released = await act(asha, item["key"], "release")
        later = await asha.get(f"{ITEMS}/{item['key']}")

    assert hidden["confidential"] is True
    assert released.status_code == 200  # was 404 and a rollback: the answer hid the item
    body = released.json()
    assert (body["status"], body["assignee"]) == ("new", None)
    assert body["allowed_actions"] == []  # she may not see it any more, so she may do nothing
    assert later.status_code == 404
    assert (await row(seeded.url, item["key"]))["assignee_id"] is None


async def test_a_confidential_compliance_item_is_worked_by_its_owner_and_hidden_from_other_members(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api, team_key="CMP", type="compliance_request", title="Data export check")
    other = "lakshmi.lead@baton.test"
    async with (
        signed_in_as(app_settings, FARAH) as farah,
        signed_in_as(app_settings, ISHAAN) as lead,
        signed_in_as(app_settings, other) as other_lead,
    ):
        before = await farah.get(f"{ITEMS}/{item['key']}")
        assigned = await ok(
            await act(
                lead,
                item["key"],
                "assign",
                {"assignee_id": (await farah.get("/api/v1/me")).json()["user"]["id"]},
                version=item["version"],
            )
        )
        blocked = await ok(await transition(farah, assigned, "block", reason="Waiting on legal"))
        approval = await requested_approval(
            farah, await ok(await transition(farah, blocked, "unblock"))
        )
        decided = await ok(await decide(other_lead, approval, "approve"))
        released = await act(farah, item["key"], "release")

    assert before.status_code == 404  # not hers yet
    assert decided["approval"]["status"] == "approved"
    assert released.status_code == 200
    assert released.json()["allowed_actions"] == []


# ----- the original of a duplicate is not revealed to someone who cannot see it ----------------


async def test_a_duplicate_does_not_name_an_original_the_viewer_cannot_see(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    mine = await new_item(api, title="Looks like the confidential one")  # Meera, a PAY viewer
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        original = await created(asha, title="Confidential original")
        await ok(await patch_item(lead, original["key"], {"confidential": True}, version=1))
        await ok(
            await transition(
                lead,
                mine,
                "close",
                reason="Same as the other one",
                resolution="duplicate",
                duplicate_of=original["key"],
            )
        )
        by_closer = (await lead.get(f"{ITEMS}/{mine['key']}")).json()
    seen_by_requester = await get(api, mine["key"])
    original_for_requester = await api.get(f"{ITEMS}/{original['key']}")

    assert original_for_requester.status_code == 404  # Meera cannot see the original...
    assert seen_by_requester["duplicate_of"] is None  # ...so its key is not shown to her either
    assert by_closer["duplicate_of"] == original["key"]  # the lead can see both


# ----- hidden items answer 404 to every command -------------------------------------------------


async def test_every_workflow_command_is_404_for_someone_who_cannot_see_the_item(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    async with signed_in_as(app_settings, ASHA) as asha:
        waiting = await requested_approval(asha, await claimed(asha, item))
    approval = waiting["approval"]["id"]
    version = waiting["version"]
    async with signed_in_as(app_settings, SAMEER) as outsider:
        answers = [
            await act(outsider, item["key"], "claim"),
            await act(outsider, item["key"], "release"),
            await act(outsider, item["key"], "assign", {"assignee_id": None}, version=version),
            await transition(outsider, waiting, "block", reason="x"),
            await act(
                outsider,
                item["key"],
                "transfer",
                {"team_key": "SRE", "reason": "x"},
                version=version,
            ),
            await act(outsider, item["key"], "approvals", {}, version=version),
            await act(
                outsider,
                item["key"],
                f"approvals/{approval}/decision",
                {"decision": "approve"},
                version=version,
            ),
            await act(outsider, item["key"], f"approvals/{approval}/cancel"),
        ]

    assert [a.status_code for a in answers] == [404] * len(answers)
    assert {a.json()["code"] for a in answers} == {"NOT_FOUND"}


async def test_a_hidden_item_is_404_at_once_even_while_another_command_holds_its_lock(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    """Waiting for the lock first would turn the 404 into a 503 and show that the item exists."""
    item = await new_item(api)
    holder = await asyncpg.connect(asyncpg_dsn(seeded.url))
    try:
        transaction = holder.transaction()
        await transaction.start()
        await holder.execute("SELECT 1 FROM work_items WHERE key = $1 FOR UPDATE", item["key"])
        async with signed_in_as(app_settings, SAMEER) as outsider:
            started = asyncio.get_running_loop().time()
            answer = await act(outsider, item["key"], "claim")
            waited = asyncio.get_running_loop().time() - started
        await transaction.rollback()
    finally:
        await holder.close()

    assert answer.status_code == 404
    assert waited < 1.5  # lock_timeout is 2 s: it did not wait for the lock


async def test_a_command_that_waited_for_a_transfer_to_commit_is_stale_not_missing(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    """The row lock used to join `teams`; after the wait Postgres re-checked the join against the
    old team and found nothing, so the requester was told her item did not exist."""
    item = await new_item(api)
    holder = await asyncpg.connect(asyncpg_dsn(seeded.url))
    try:
        transaction = holder.transaction()
        await transaction.start()
        await holder.execute(
            "UPDATE work_items SET team_id = (SELECT id FROM teams WHERE key = 'SRE'), "
            "version = version + 1 WHERE key = $1",
            item["key"],
        )
        waiting = asyncio.create_task(transition(api, item, "withdraw", reason="Raised by mistake"))
        await asyncio.sleep(0.5)  # let it queue behind the lock
        await transaction.commit()
        answer = await waiting
    finally:
        await holder.close()

    assert answer.status_code == 412
    assert answer.json()["code"] == "VERSION_CONFLICT"


# ----- idempotent replays of the new endpoints (I6) ---------------------------------------------


async def test_a_replayed_claim_gets_the_first_answer_and_writes_nothing_more(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    key = new_key()
    async with signed_in_as(app_settings, ASHA) as asha:
        first = await act(asha, item["key"], "claim", idempotency_key=key)
        again = await act(asha, item["key"], "claim", idempotency_key=key)

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert "Idempotent-Replayed" not in first.headers
    assert again.json() == first.json()
    assert [e["kind"] for e in await events(seeded.url, item["key"])].count("assigned") == 1


async def test_a_lost_claim_is_replayed_as_the_same_409(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    item = await new_item(api)
    key = new_key()
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, VIKRAM) as late:
        await claimed(asha, item)
        first = await act(late, item["key"], "claim", idempotency_key=key)
        again = await act(late, item["key"], "claim", idempotency_key=key)

    assert (first.status_code, again.status_code) == (409, 409)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    assert again.json()["claimed_by"]["name"] == "Asha Rao"


async def test_a_replayed_approval_decision_applies_once(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    key = new_key()
    async with signed_in_as(app_settings, ASHA) as asha, signed_in_as(app_settings, PRIYA) as lead:
        waiting = await requested_approval(asha, await claimed(asha, item))
        path = f"approvals/{waiting['approval']['id']}/decision"
        first = await act(
            lead,
            item["key"],
            path,
            {"decision": "approve"},
            version=waiting["version"],
            idempotency_key=key,
        )
        again = await act(
            lead,
            item["key"],
            path,
            {"decision": "approve"},
            version=waiting["version"],
            idempotency_key=key,
        )

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    kinds = [e["kind"] for e in await events(seeded.url, item["key"])]
    assert kinds.count("approval_approved") == 1


async def test_a_replayed_transition_and_request_do_not_repeat_and_a_reused_key_is_422(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api, type="payment_investigation")
    ask_key, block_key = new_key(), new_key()
    async with signed_in_as(app_settings, ASHA) as asha:
        owned = await claimed(asha, item)
        asked = await act(
            asha, item["key"], "approvals", {}, version=owned["version"], idempotency_key=ask_key
        )
        asked_again = await act(
            asha, item["key"], "approvals", {}, version=owned["version"], idempotency_key=ask_key
        )
        reused = await act(
            asha,
            item["key"],
            "transition",
            {"action": "block", "reason": "x"},
            version=owned["version"],
            idempotency_key=ask_key,
        )
        cancelled = await act(
            asha,
            item["key"],
            f"approvals/{asked.json()['approval']['id']}/cancel",
            idempotency_key=block_key,
        )
        cancelled_again = await act(
            asha,
            item["key"],
            f"approvals/{asked.json()['approval']['id']}/cancel",
            idempotency_key=block_key,
        )

    assert (asked.status_code, asked_again.status_code) == (201, 201)
    assert asked_again.headers["Idempotent-Replayed"] == "true"
    assert reused.status_code == 422
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert (cancelled.status_code, cancelled_again.status_code) == (200, 200)
    kinds = [e["kind"] for e in await events(seeded.url, item["key"])]
    assert (kinds.count("approval_requested"), kinds.count("approval_cancelled")) == (1, 1)


async def test_a_replayed_transfer_returns_the_stored_body_to_a_lead_who_can_no_longer_see_it(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    item = await new_item(api)
    key = new_key()
    async with signed_in_as(app_settings, PRIYA) as lead:
        first = await act(
            lead,
            item["key"],
            "transfer",
            {"team_key": "SRE", "reason": "Wrong team"},
            version=item["version"],
            idempotency_key=key,
        )
        again = await act(
            lead,
            item["key"],
            "transfer",
            {"team_key": "SRE", "reason": "Wrong team"},
            version=item["version"],
            idempotency_key=key,
        )

    assert (first.status_code, again.status_code) == (200, 200)
    assert again.headers["Idempotent-Replayed"] == "true"
    assert again.json() == first.json()
    kinds = [e["kind"] for e in await events(seeded.url, item["key"])]
    assert kinds.count("transferred") == 1
    assert uuid.UUID(again.json()["id"]) == uuid.UUID(item["id"])
