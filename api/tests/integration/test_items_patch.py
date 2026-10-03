"""PATCH /items/{key} (SPEC 4.2, 6.2): who may edit what, versions, reasons, history."""

from datetime import datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from tests.integration.conftest import csrf_headers
from tests.integration.test_items_create_read import DEV_VIEWER
from tests.integration.test_teams_members import ASHA, MEERA, PRIYA, signed_in
from tests.support.items import (
    ITEMS,
    create_item,
    created,
    edited,
    events_of,
    new_key,
    patch_item,
    scalar,
    signed_in_as,
)
from tests.support.seeded import SeededDatabase
from tests.support.users import execute


async def outbox_count(seeded: SeededDatabase) -> int:
    return int(await scalar(seeded.url, "SELECT count(*) FROM outbox"))


# ----- preconditions ----------------------------------------------------------------------


async def test_patch_without_if_match_is_428(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    response = await patch_item(api, item["key"], {"title": "A new title"}, version=None)

    assert response.status_code == 428
    assert response.json()["code"] == "PRECONDITION_REQUIRED"


async def test_a_malformed_if_match_is_a_400_not_a_428(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    for bad in ("*", "seven", '"1.5"', '""'):
        response = await patch_item(api, item["key"], {"title": "A new title"}, version=bad)

        assert response.status_code == 400, bad
        assert response.json()["code"] == "VALIDATION_FAILED"


async def test_if_match_works_quoted_bare_or_weak(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    for index, header in enumerate(['"1"', "2", 'W/"3"']):
        response = await patch_item(
            api, item["key"], {"title": f"Title {index} here"}, version=header
        )

        assert response.status_code == 200, header
        assert response.json()["version"] == index + 2


async def test_bad_patch_bodies_are_400(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    bad: list[dict[str, Any]] = [
        {},
        {"reason": "only a reason"},
        {"title": None},
        {"title": "ab"},
        {"priority": 7},
        {"due_at": "2030-01-01T00:00:00"},  # no time zone
        {"surprise": 1},
        {"apply_type_defaults": True},
    ]

    for changes in bad:
        response = await patch_item(api, item["key"], changes, version=item["version"])

        assert response.status_code == 400, changes
        assert response.json()["code"] == "VALIDATION_FAILED"


# ----- the happy path and its history -----------------------------------------------------


async def test_an_edit_bumps_the_version_by_one_and_writes_one_event_with_that_version(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    response = await patch_item(
        api,
        item["key"],
        {"title": "Refund stuck, order 48213", "description": "More detail"},
        version=item["version"],
    )

    assert response.status_code == 200
    updated = response.json()
    assert updated["version"] == 2
    assert response.headers["ETag"] == '"2"'
    assert updated["title"] == "Refund stuck, order 48213"
    events = await events_of(seeded.url, item["key"])
    assert [(e["kind"], e["item_version"]) for e in events] == [
        ("created", 1),
        ("field_changed", 2),
    ]
    assert updated["last_event_id"] == events[-1]["id"]
    assert updated["last_event"]["kind"] == "field_changed"
    assert datetime.fromisoformat(updated["updated_at"]) > datetime.fromisoformat(
        item["updated_at"]
    )


async def test_several_changes_in_one_request_are_one_version_and_several_events(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, priority=2)
    async with signed_in_as(app_settings, PRIYA) as lead:
        response = await patch_item(
            lead,
            item["key"],
            {"title": "A new title", "priority": 0, "confidential": True},
            version=item["version"],
        )

    assert response.status_code == 200
    assert response.json()["version"] == 2  # one command, one bump
    events = await events_of(seeded.url, item["key"])
    assert [(e["kind"], e["item_version"]) for e in events] == [
        ("created", 1),
        ("field_changed", 2),
        ("priority_changed", 2),
        ("confidential_changed", 2),
    ]
    ids = [e["id"] for e in events]
    assert ids == sorted(ids)


async def test_events_over_a_series_of_edits_have_contiguous_versions(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    for index in range(5):
        item = await edited(api, item, {"title": f"Edit number {index} of the title"})

    versions = [e["item_version"] for e in await events_of(seeded.url, item["key"])]
    assert versions == [1, 2, 3, 4, 5, 6]
    assert item["version"] == 6


async def test_an_edit_that_changes_nothing_is_a_200_with_no_event_no_version_no_outbox(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    outbox_before = await outbox_count(seeded)

    response = await patch_item(
        api,
        item["key"],
        {"title": item["title"], "priority": item["priority"]},
        version=item["version"],
    )

    assert response.status_code == 200
    assert response.json() == item
    assert len(await events_of(seeded.url, item["key"])) == 1
    assert await outbox_count(seeded) == outbox_before


async def test_every_change_gets_an_outbox_row_in_the_same_transaction(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    before = await outbox_count(seeded)

    await edited(api, item, {"title": "Another good title"})

    assert await outbox_count(seeded) == before + 1


# ----- who may change what ----------------------------------------------------------------


async def test_a_viewer_cannot_edit_anything_and_nothing_is_written(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    outbox_before = await outbox_count(seeded)

    async with signed_in_as(app_settings, DEV_VIEWER) as viewer:
        response = await patch_item(viewer, item["key"], {"title": "Hijacked"}, version=1)

    assert response.status_code == 403
    assert response.json()["code"] == "FORBIDDEN"
    assert "edit the title" in response.json()["detail"]
    unchanged = (await api.get(f"{ITEMS}/{item['key']}")).json()
    assert unchanged["version"] == 1
    assert len(await events_of(seeded.url, item["key"])) == 1
    assert await outbox_count(seeded) == outbox_before


async def test_someone_who_cannot_see_the_item_gets_404_not_403(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, team_key="CMP", type="compliance_request")

    async with signed_in_as(app_settings, ASHA) as outsider:  # a Payments member
        response = await patch_item(outsider, item["key"], {"title": "Peeking"}, version=1)

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_a_member_can_change_priority_but_not_the_type_or_the_due_date(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)

    async with signed_in_as(app_settings, ASHA) as member:
        ok = await patch_item(member, item["key"], {"priority": 1}, version=1)
        type_change = await patch_item(member, item["key"], {"type": "engineering"}, version=2)
        due = await patch_item(member, item["key"], {"due_at": "2030-01-01T00:00:00Z"}, version=2)

    assert ok.status_code == 200
    assert (type_change.status_code, due.status_code) == (403, 403)
    assert "Only leads" in type_change.json()["detail"]


async def test_the_requester_may_change_priority_only_while_new_and_unassigned(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    first = await patch_item(api, item["key"], {"priority": 1}, version=1)

    await execute(
        seeded.url,
        "UPDATE work_items SET assignee_id = (SELECT id FROM users WHERE email = $1), "
        "status = 'in_progress', version = version + 1 WHERE key = $2",
        ASHA,
        item["key"],
    )
    second = await patch_item(api, item["key"], {"priority": 3}, version=3)

    assert first.status_code == 200
    assert second.status_code == 403
    assert "only while the item is new and unassigned" in second.json()["detail"]


async def test_the_requester_cannot_edit_text_after_it_is_resolved(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    await execute(
        seeded.url,
        "UPDATE work_items SET assignee_id = (SELECT id FROM users WHERE email = $1), "
        "status = 'resolved', resolution = 'done', version = version + 1 WHERE key = $2",
        ASHA,
        item["key"],
    )

    response = await patch_item(api, item["key"], {"title": "Too late to rename"}, version=2)

    assert response.status_code == 403


# ----- reasons and decisions --------------------------------------------------------------


async def test_lowering_a_p1_needs_a_reason_and_then_is_a_decision(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, priority=1)
    async with signed_in_as(app_settings, PRIYA) as lead:
        without = await patch_item(lead, item["key"], {"priority": 3}, version=1)
        with_reason = await patch_item(
            lead,
            item["key"],
            {"priority": 3, "reason": "Customer confirmed it is fine"},
            version=1,
        )

    assert without.status_code == 422
    assert without.json()["code"] == "REASON_REQUIRED"
    assert with_reason.status_code == 200
    events = await events_of(seeded.url, item["key"])
    assert [e["kind"] for e in events] == ["created", "priority_changed"]
    assert events[-1]["is_decision"] is True
    assert events[-1]["reason"] == "Customer confirmed it is fine"
    assert (
        await scalar(seeded.url, "SELECT version FROM work_items WHERE key = $1", item["key"])
    ) == 2


async def test_raising_the_priority_needs_no_reason_and_moves_an_automatic_due_date(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, priority=3)

    updated = await edited(api, item, {"priority": 0})

    made = datetime.fromisoformat(updated["created_at"])
    assert datetime.fromisoformat(updated["due_at"]) - made == timedelta(hours=4)
    assert updated["due_source"] == "auto"
    last = (await events_of(seeded.url, item["key"]))[-1]
    assert last["is_decision"] is False
    assert last["data"] is not None


async def test_a_leads_due_date_sticks_and_a_later_priority_change_leaves_it(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    target = "2031-05-05T10:00:00+00:00"
    async with signed_in_as(app_settings, PRIYA) as lead:
        item = await edited(lead, item, {"due_at": target})
        item = await edited(lead, item, {"priority": 0})

    assert datetime.fromisoformat(item["due_at"]) == datetime.fromisoformat(target)
    assert item["due_source"] == "manual"


async def test_turning_approval_off_needs_a_lead_and_a_reason_and_is_a_decision(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, type="payment_investigation")
    assert item["requires_approval"] is True
    async with signed_in_as(app_settings, ASHA) as member:
        by_member = await patch_item(member, item["key"], {"requires_approval": False}, version=1)
    async with signed_in_as(app_settings, PRIYA) as lead:
        no_reason = await patch_item(lead, item["key"], {"requires_approval": False}, version=1)
        done = await patch_item(
            lead,
            item["key"],
            {"requires_approval": False, "reason": "Small refund, low risk"},
            version=1,
        )

    assert by_member.status_code == 403
    assert no_reason.status_code == 422
    assert done.status_code == 200
    assert done.json()["requires_approval"] is False
    last = (await events_of(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"]) == ("requires_approval_changed", True)


async def test_turning_approval_on_is_for_the_assignee_or_a_lead_not_the_requester(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    by_requester = await patch_item(api, item["key"], {"requires_approval": True}, version=1)
    async with signed_in_as(app_settings, PRIYA) as lead:
        by_lead = await patch_item(lead, item["key"], {"requires_approval": True}, version=1)

    assert by_requester.status_code == 403
    assert by_lead.status_code == 200
    assert by_lead.json()["requires_approval"] is True


async def test_confidential_is_a_lead_decision_event(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        response = await patch_item(lead, item["key"], {"confidential": True}, version=1)

    assert response.status_code == 200
    last = (await events_of(seeded.url, item["key"]))[-1]
    assert (last["kind"], last["is_decision"]) == ("confidential_changed", True)


async def test_changing_the_type_can_reapply_the_new_types_defaults_when_asked(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, type="incident")
    async with signed_in_as(app_settings, PRIYA) as lead:
        plain = await edited(lead, item, {"type": "payment_investigation"})
        again = await edited(
            lead, plain, {"type": "compliance_request", "apply_type_defaults": True}
        )

    assert (plain["requires_approval"], plain["confidential"]) == (False, False)
    assert (again["requires_approval"], again["confidential"]) == (True, True)


# ----- state rules ------------------------------------------------------------------------


async def test_requires_approval_cannot_change_while_awaiting_approval(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, type="payment_investigation")
    await execute(
        seeded.url,
        "UPDATE work_items SET assignee_id = (SELECT id FROM users WHERE email = $1), "
        "status = 'awaiting_approval', version = version + 1 WHERE key = $2",
        ASHA,
        item["key"],
    )
    async with signed_in_as(app_settings, PRIYA) as lead:
        response = await patch_item(
            lead, item["key"], {"requires_approval": False, "reason": "Skip it"}, version=2
        )

    assert response.status_code == 409
    assert response.json()["code"] == "WORKFLOW_VIOLATION"
    assert "Cancel the approval" in response.json()["detail"]


async def test_editing_the_text_of_an_item_with_an_approval_is_refused_until_phase_4(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, type="payment_investigation")
    await execute(
        seeded.url,
        "INSERT INTO approvals "
        "(item_id, status, requested_by, subject_hash, decided_by, decided_at) "
        "SELECT w.id, 'approved', w.requester_id, 'hash', u.id, now() FROM work_items w, users u "
        "WHERE w.key = $1 AND u.email = $2",
        item["key"],
        PRIYA,
    )
    async with signed_in_as(app_settings, PRIYA) as lead:
        text = await patch_item(lead, item["key"], {"title": "Changed after approval"}, version=1)
        priority = await patch_item(lead, item["key"], {"priority": 0}, version=1)
        view = (await lead.get(f"{ITEMS}/{item['key']}")).json()

    assert text.status_code == 409
    assert "approval on record" in text.json()["detail"]
    assert priority.status_code == 200  # not a material field
    assert "edit_text" not in view["allowed_actions"]  # the UI is told not to offer it
    assert "edit_type" not in view["allowed_actions"]


# ----- stale writes (T-STALE, one request at a time) ---------------------------------------


async def test_a_stale_edit_is_412_with_the_item_now_and_exactly_what_changed(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, priority=2)
    async with signed_in_as(app_settings, ASHA) as other:
        theirs = await patch_item(other, item["key"], {"priority": 1}, version=1)
    assert theirs.status_code == 200

    mine = await patch_item(api, item["key"], {"title": "My rename of it"}, version=1)

    assert mine.status_code == 412
    problem = mine.json()
    assert problem["code"] == "VERSION_CONFLICT"
    assert problem["current"]["version"] == 2
    assert problem["current"]["priority"] == 1
    assert [(e["kind"], e["item_version"]) for e in problem["changes_since"]] == [
        ("priority_changed", 2)
    ]
    assert problem["changes_since"][0]["actor"]["name"] == "Asha Rao"
    assert problem["changes_since"][0]["data"]["priority"] == {"from": 2, "to": 1}
    assert (await api.get(f"{ITEMS}/{item['key']}")).json()["title"] == item["title"]  # not applied


async def test_comments_and_system_notes_are_not_in_changes_since(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    await execute(
        seeded.url,
        "INSERT INTO item_events (item_id, team_id, kind, item_version) "
        "SELECT id, team_id, 'sla_breached', version FROM work_items WHERE key = $1",
        item["key"],
    )
    async with signed_in_as(app_settings, ASHA) as other:
        await patch_item(other, item["key"], {"priority": 1}, version=1)

    mine = await patch_item(api, item["key"], {"title": "My rename of it"}, version=1)

    assert [e["kind"] for e in mine.json()["changes_since"]] == ["priority_changed"]


async def test_a_stale_edit_by_someone_who_may_not_edit_is_403_not_412(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        await patch_item(lead, item["key"], {"priority": 1}, version=1)
    async with signed_in_as(app_settings, DEV_VIEWER) as viewer:
        response = await patch_item(viewer, item["key"], {"title": "Not allowed"}, version=1)

    assert response.status_code == 403  # permission is judged before the version, so no peeking


async def test_a_failed_edit_writes_no_event_and_no_outbox_row(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api, priority=0)
    before = await outbox_count(seeded)
    async with signed_in_as(app_settings, PRIYA) as lead:
        refused = await patch_item(lead, item["key"], {"priority": 3}, version=1)  # no reason
        stale = await patch_item(lead, item["key"], {"title": "A fine title"}, version=9)

    assert (refused.status_code, stale.status_code) == (422, 412)
    assert len(await events_of(seeded.url, item["key"])) == 1
    assert await outbox_count(seeded) == before
    assert (
        await scalar(seeded.url, "SELECT version FROM work_items WHERE key = $1", item["key"]) == 1
    )


# ----- idempotent edits -------------------------------------------------------------------


async def test_a_retried_patch_with_the_same_key_gets_the_first_answer_not_a_412(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    key = new_key()

    first = await patch_item(
        api, item["key"], {"title": "Retry safe title"}, version=1, idempotency_key=key
    )
    retry = await patch_item(
        api, item["key"], {"title": "Retry safe title"}, version=1, idempotency_key=key
    )

    assert (first.status_code, retry.status_code) == (200, 200)
    assert retry.json() == first.json()
    assert "Idempotent-Replayed" not in first.headers
    assert retry.headers["Idempotent-Replayed"] == "true"
    assert retry.headers["ETag"] == '"2"'
    assert len(await events_of(seeded.url, item["key"])) == 2  # one edit, not two


async def test_the_same_patch_key_for_a_different_change_is_422(api: httpx.AsyncClient) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    key = new_key()
    await patch_item(
        api, item["key"], {"title": "First title here"}, version=1, idempotency_key=key
    )

    other = await patch_item(
        api, item["key"], {"title": "Another title here"}, version=2, idempotency_key=key
    )

    assert other.status_code == 422
    assert other.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


async def test_a_stale_patch_is_replayed_too_so_a_retry_sees_the_same_412(
    api: httpx.AsyncClient, app_settings: Settings
) -> None:
    await signed_in(api, MEERA)
    item = await created(api)
    async with signed_in_as(app_settings, PRIYA) as lead:
        await patch_item(lead, item["key"], {"priority": 1}, version=1)
    key = new_key()

    first = await patch_item(
        api, item["key"], {"title": "Stale rename"}, version=1, idempotency_key=key
    )
    retry = await patch_item(
        api, item["key"], {"title": "Stale rename"}, version=1, idempotency_key=key
    )

    assert (first.status_code, retry.status_code) == (412, 412)
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"


async def test_create_without_key_is_refused_but_patch_without_key_still_works(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, MEERA)
    refused = await api.post(
        ITEMS,
        json={"team_key": "PAY", "type": "incident", "title": "No key at all"},
        headers=csrf_headers(api),
    )
    item = await created(api)

    plain = await patch_item(api, item["key"], {"title": "No key needed here"}, version=1)

    assert refused.status_code == 400
    assert plain.status_code == 200
    assert (await create_item(api)).status_code == 201
