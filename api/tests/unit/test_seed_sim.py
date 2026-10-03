"""The seed simulator may only take transitions SPEC 4.1 allows, and records every change."""

from datetime import timedelta

import pytest

from app.domain.enums import ApprovalStatus, EventKind, ItemStatus, Resolution
from scripts.seedlib.sim import InvalidTransition

from .seed_fixtures import (
    LEAD_A,
    MEMBER_A,
    MEMBER_B,
    REQUESTER,
    T0,
    at,
    kinds,
    make_item,
    make_team,
    status_of,
    uid,
)


def test_creation_writes_one_created_event_at_version_one() -> None:
    item = make_item()

    assert item.version == 1
    assert [e.kind for e in item.events] == [EventKind.CREATED]
    assert item.status == ItemStatus.NEW
    assert item.key == "PAY-1"


def test_a_command_with_two_events_uses_one_version() -> None:
    item = make_item()

    item.claim(at(5), MEMBER_A)

    assert item.version == 2
    assert kinds(item, 2) == [EventKind.ASSIGNED, EventKind.STATUS_CHANGED]


def test_a_comment_writes_an_event_but_does_not_bump_the_version() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)
    before = item.version

    item.comment(at(6), MEMBER_B, "Looking at this too.")

    assert item.version == before
    comment_event = item.events[-1]
    assert comment_event.kind == EventKind.COMMENTED
    assert comment_event.version == before  # carries the current version
    assert item.last_activity_at == at(6)  # a person wrote it
    assert item.updated_at == at(6)
    assert item.watchers >= {MEMBER_B}


def test_system_events_do_not_bump_the_version_or_reset_going_stale() -> None:
    item = make_item(priority=0)
    item.claim(at(5), MEMBER_A)
    version, activity = item.version, item.last_activity_at

    item.suggest_duplicates(at(6), ["PAY-7"])
    item.breach(at(300))

    assert item.version == version
    assert [e.version for e in item.events[-2:]] == [version, version]
    assert [e.actor for e in item.events[-2:]] == [None, None]
    assert item.last_activity_at == activity  # machine events are not activity
    assert item.updated_at == at(300)
    assert item.sla_breached_at == at(300)


def test_the_next_change_after_a_comment_bumps_from_the_unchanged_version() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)
    item.comment(at(6), MEMBER_B, "hello")

    item.block(at(7), MEMBER_A, "Waiting on the bank")

    assert item.version == 3  # created 1, claim 2, comment 2, block 3
    assert [e.version for e in item.events] == [1, 2, 2, 2, 3]


def test_claiming_twice_is_not_a_valid_transition() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)

    with pytest.raises(InvalidTransition, match="claim"):
        item.claim(at(6), MEMBER_B)


def test_resolve_needs_a_valid_approval_when_the_item_requires_one() -> None:
    item = make_item(requires_approval=True)
    item.claim(at(5), MEMBER_A)

    with pytest.raises(InvalidTransition, match="approval"):
        item.resolve(at(10), MEMBER_A, "done")

    item.request_approval(at(11), MEMBER_A, None)
    item.decide(at(12), LEAD_A, approve=True, note="ok")
    item.resolve(at(13), MEMBER_A, "done")

    assert item.status == ItemStatus.RESOLVED
    assert item.resolution == Resolution.DONE


def test_the_requester_of_an_approval_cannot_approve_it() -> None:
    item = make_item(requires_approval=True)
    item.claim(at(5), LEAD_A)
    item.request_approval(at(6), LEAD_A, None)

    with pytest.raises(AssertionError):
        item.decide(at(7), LEAD_A, approve=True, note="my own request")


def test_editing_the_title_invalidates_an_approval_and_blocks_resolving() -> None:
    item = make_item(requires_approval=True)
    item.claim(at(5), MEMBER_A)
    item.request_approval(at(6), MEMBER_A, None)
    item.decide(at(7), LEAD_A, approve=True, note="ok")
    assert item.has_valid_approval()

    item.edit_text(at(8), MEMBER_A, "title", "Refund stuck for order 99999")

    assert not item.has_valid_approval()
    assert item.approvals[0].status == ApprovalStatus.INVALIDATED
    assert kinds(item, item.version) == [EventKind.FIELD_CHANGED, EventKind.APPROVAL_INVALIDATED]
    with pytest.raises(InvalidTransition, match="approval"):
        item.resolve(at(9), MEMBER_A, "done")


def test_editing_while_awaiting_cancels_the_review_and_returns_to_in_progress() -> None:
    item = make_item(requires_approval=True)
    item.claim(at(5), MEMBER_A)
    item.request_approval(at(6), MEMBER_A, None)
    assert status_of(item) == ItemStatus.AWAITING_APPROVAL

    item.edit_text(at(7), MEMBER_A, "description", "Customer was charged three times.")

    assert status_of(item) == ItemStatus.IN_PROGRESS
    assert item.pending_approval() is None
    assert item.approvals[0].status == ApprovalStatus.INVALIDATED


def test_nothing_that_needs_an_owner_can_happen_while_awaiting_approval() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)
    item.request_approval(at(6), MEMBER_A, None)

    with pytest.raises(InvalidTransition):
        item.release(at(7), MEMBER_A)
    with pytest.raises(InvalidTransition):
        item.assign(at(7), LEAD_A, MEMBER_B)
    with pytest.raises(InvalidTransition):
        item.release(at(7), LEAD_A, by_lead=True)


def test_lowering_priority_from_p1_needs_a_reason_and_leaves_state_untouched() -> None:
    item = make_item(priority=1)

    with pytest.raises(InvalidTransition, match="reason"):
        item.set_priority(at(5), MEMBER_A, 3)

    assert item.priority == 1
    assert item.version == 1
    item.set_priority(at(6), MEMBER_A, 3, "Workaround in place.")
    assert item.events[-1].is_decision


def test_raising_priority_is_not_a_decision_and_recomputes_the_due_date() -> None:
    item = make_item(priority=2)

    item.set_priority(at(5), MEMBER_A, 0)

    event = item.events[-1]
    assert not event.is_decision
    assert item.due_at == T0 + timedelta(hours=4)
    assert event.data["due_at"]["to"] == item.due_at.isoformat()


def test_a_manual_due_date_survives_priority_changes() -> None:
    item = make_item(priority=2)
    manual = T0 + timedelta(days=30)
    item.set_due(at(5), LEAD_A, manual)

    item.set_priority(at(6), MEMBER_A, 0)

    assert item.due_at == manual


def test_transfer_keeps_the_key_moves_the_team_and_drops_an_owner_who_is_not_there() -> None:
    other = make_team("SUP", n=200)
    item = make_item(requires_approval=True)
    item.claim(at(5), MEMBER_A)
    item.request_approval(at(6), MEMBER_A, None)

    item.transfer(at(7), LEAD_A, other, "Routed to the wrong team.")

    assert item.key == "PAY-1"
    assert item.team is other
    assert item.assignee is None
    assert item.status == ItemStatus.NEW
    assert item.approvals[0].status == ApprovalStatus.CANCELLED
    assert [e.kind for e in item.events if e.version == item.version] == [
        EventKind.APPROVAL_CANCELLED,
        EventKind.TRANSFERRED,
    ]
    # every event of the command carries the team the item has afterwards
    assert [e.team_id for e in item.events if e.version == item.version] == [other.id, other.id]
    assert item.events[0].team_id == uid(100)  # earlier events keep the team they were written in


def test_reopen_returns_to_the_previous_owner_when_they_can_still_work_it() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)
    item.resolve(at(6), MEMBER_A, "Fixed.")

    item.reopen(at(7), REQUESTER, "Problem is back.")

    assert item.status == ItemStatus.IN_PROGRESS
    assert item.assignee == MEMBER_A


def test_reopen_lands_in_new_when_the_previous_owner_left_the_team() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)
    item.resolve(at(6), MEMBER_A, "Fixed.")
    item.team.members.remove(MEMBER_A)
    item.team.finish()

    item.reopen(at(7), LEAD_A, "Problem is back.")

    assert item.status == ItemStatus.NEW
    assert item.assignee is None
    assert item.resolution is None


def test_only_a_new_item_can_be_withdrawn() -> None:
    item = make_item()
    item.claim(at(5), MEMBER_A)

    with pytest.raises(InvalidTransition, match="withdraw"):
        item.withdraw(at(6), "Changed my mind.")


def test_withdraw_closes_a_new_item_as_wont_do() -> None:
    item = make_item()

    item.withdraw(at(5), "Changed my mind.")

    assert item.status == ItemStatus.CLOSED
    assert item.resolution == Resolution.WONT_DO
    assert item.events[-1].actor == REQUESTER


def test_time_cannot_go_backwards() -> None:
    item = make_item()
    item.claim(at(10), MEMBER_A)

    with pytest.raises(AssertionError, match="backwards"):
        item.comment(at(5), MEMBER_B, "late")
