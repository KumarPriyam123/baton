"""Seeded histories are valid for many seeds, and the verifier really catches broken ones."""

import copy
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain.enums import Resolution
from scripts.seedlib import generate as g
from scripts.seedlib.verify import check_item

from .seed_fixtures import LEAD_A, MEMBER_A, REQUESTER, at, make_item, make_team
from .seed_records import records_from_dataset, records_from_sim

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def build(seed: int) -> g.Dataset:
    return g.build_dataset("demo", seed=seed, now=NOW, password_hash="x")


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 42, 2026])
def test_every_item_of_a_demo_seed_replays_to_its_stored_state(seed: int) -> None:
    items, events, approvals, reads = records_from_dataset(build(seed))

    problems = [p for i in items for p in check_item(items[i], events[i], approvals[i], reads[i])]

    assert problems == []
    assert len(items) == 600


def test_read_markers_point_at_events_of_the_same_item() -> None:
    items, events, _, reads = records_from_dataset(build(3))

    markers = [(i, r["last_read_event_id"]) for i in items for r in reads[i]]

    assert len(markers) > 300
    for item_id, marker in markers:
        assert marker in {e["id"] for e in events[item_id]}
    behind = sum(marker != events[i][-1]["id"] for i, marker in markers)
    assert behind > 0  # some items have news since the reader last looked


def test_the_same_seed_and_time_give_identical_data() -> None:
    first, second = build(5), build(5)

    assert first.items == second.items
    assert first.events == second.events
    assert first.counts() == second.counts()


def test_a_different_seed_gives_different_data() -> None:
    assert build(5).items != build(6).items


# ----- the verifier itself: each kind of corruption must be reported ------------------------


def valid_history() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    item = make_item(requires_approval=True)
    item.claim(at(5), MEMBER_A)
    item.comment(at(6), MEMBER_A, "Checking the ledger.")
    item.request_approval(at(7), MEMBER_A, "please")
    item.decide(at(8), LEAD_A, approve=True, note="ok")
    item.resolve(at(9), MEMBER_A, "Fixed.")
    item.close(at(10), LEAD_A, resolution=Resolution.DONE, reason="Confirmed.")
    item.comment(at(11), REQUESTER, "Thanks!")
    assert item.requester == REQUESTER
    return records_from_sim(item)


def transfer_history() -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    item = make_item(requires_approval=True)
    item.claim(at(5), MEMBER_A)
    item.request_approval(at(6), MEMBER_A, None)
    item.transfer(at(7), LEAD_A, make_team("SUP", n=200), "Routed to the wrong team.")
    return records_from_sim(item)


def test_a_transfer_history_is_valid_and_stamps_events_with_the_new_team() -> None:
    item, events, approvals = transfer_history()

    assert check_item(item, events, approvals) == []
    last_command = [e for e in events if e["item_version"] == item["version"]]
    assert {e["team_id"] for e in last_command} == {item["team_id"]}


def test_verifier_rejects_an_event_stamped_with_the_old_team_in_a_transfer() -> None:
    item, events, approvals = transfer_history()
    cancelled = next(e for e in events if e["kind"] == "approval_cancelled")
    cancelled["team_id"] = events[0]["team_id"]  # the team before the transfer

    problems = check_item(item, events, approvals)

    assert any("lacks the item's team" in p for p in problems)


def test_the_baseline_history_is_valid() -> None:
    item, events, approvals = valid_history()

    assert check_item(item, events, approvals) == []


def corrupt(change: str) -> list[str]:
    item, events, approvals = copy.deepcopy(valid_history())
    reads: list[dict[str, Any]] = []
    if change == "missing_event":
        del events[2]
    elif change == "version_gap":
        for event in events[3:]:
            event["item_version"] += 1
    elif change == "comment_bumps_version":
        position = next(n for n, e in enumerate(events) if e["kind"] == "commented")
        for event in events[position:]:
            event["item_version"] += 1
    elif change == "change_after_comment_reuses_version":
        position = next(n for n, e in enumerate(events) if e["kind"] == "commented")
        for event in events[position + 1 :]:
            event["item_version"] -= 1
        item["version"] -= 1
    elif change == "stored_status":
        item["status"] = "in_progress"
    elif change == "stored_version":
        item["version"] += 1
    elif change == "decision_without_reason":
        next(e for e in events if e["kind"] == "approval_approved")["reason"] = " "
    elif change == "decision_flag_missing":
        next(e for e in events if e["kind"] == "closed")["is_decision"] = False
    elif change == "approval_removed":
        # resolved needs an approval whose approving event is gone
        events[:] = [e for e in events if e["kind"] != "approval_approved"]
        for n, event in enumerate(events):
            event["item_version"] = 1 + max(0, n - 1)
    elif change == "wrong_team_stamp":
        events[1]["team_id"] = REQUESTER
    elif change == "system_event_with_actor":
        events.append({**events[-1], "kind": "sla_breached", "id": 99, "actor_id": REQUESTER})
    elif change == "approval_status":
        approvals[0]["status"] = "pending"
    elif change == "read_marker_of_another_item":
        reads.append({"user_id": REQUESTER, "last_read_event_id": 987_654})
    else:
        raise AssertionError(change)
    return check_item(item, events, approvals, reads)


@pytest.mark.parametrize(
    "change",
    [
        "missing_event",
        "version_gap",
        "comment_bumps_version",
        "change_after_comment_reuses_version",
        "stored_status",
        "stored_version",
        "decision_without_reason",
        "decision_flag_missing",
        "approval_removed",
        "wrong_team_stamp",
        "system_event_with_actor",
        "approval_status",
        "read_marker_of_another_item",
    ],
)
def test_verifier_reports_corruption(change: str) -> None:
    assert corrupt(change), f"verifier accepted a history with: {change}"


def test_verifier_flags_an_item_without_events() -> None:
    item, _, _ = valid_history()

    assert check_item(item, [], []) == [f"{item['key']}: has no events"]
