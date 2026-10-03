"""SPEC 3.1, 4.2, 4.5: the pure rules for creating, editing and describing an item."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.domain import workflow
from app.domain.enums import DueSource, EventKind, ItemStatus, ItemType
from app.domain.errors import ReasonRequired, ValidationFailed, WorkflowViolation
from app.domain.policy import Action

CREATED = datetime(2025, 3, 1, 9, 0, tzinfo=UTC)
NOW = CREATED + timedelta(hours=10)


def state(**overrides: Any) -> workflow.ItemState:
    values: dict[str, Any] = {
        "type": ItemType.INCIDENT,
        "title": "Refund stuck",
        "description": "Customer charged twice.",
        "priority": 2,
        "status": ItemStatus.IN_PROGRESS,
        "due_at": CREATED + timedelta(hours=72),
        "due_source": DueSource.AUTO,
        "created_at": CREATED,
        "confidential": False,
        "requires_approval": False,
    }
    values.update(overrides)
    return workflow.ItemState(**values)


def plan(requested: dict[str, Any], **overrides: Any) -> workflow.EditPlan:
    options: dict[str, Any] = {
        "apply_type_defaults": False,
        "reason": None,
        "has_live_approval": False,
    }
    item_overrides = {k: overrides.pop(k) for k in list(overrides) if k.startswith("item_")}
    options.update(overrides)
    return workflow.plan_edit(
        state(**{k.removeprefix("item_"): v for k, v in item_overrides.items()}),
        requested,
        **options,
    )


# ----- creation ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("item_type", "requires_approval", "confidential"),
    [
        (ItemType.PAYMENT_INVESTIGATION, True, False),
        (ItemType.COMPLIANCE_REQUEST, True, True),
        (ItemType.OPS_TASK, False, False),
        (ItemType.INCIDENT, False, False),
        (ItemType.CUSTOMER_ISSUE, False, False),
        (ItemType.ENGINEERING, False, False),
    ],
)
def test_type_defaults_follow_the_spec_table(
    item_type: ItemType, requires_approval: bool, confidential: bool
) -> None:
    defaults = workflow.type_defaults(item_type)

    assert (defaults.requires_approval, defaults.confidential) == (requires_approval, confidential)


@pytest.mark.parametrize(("priority", "hours"), [(0, 4), (1, 24), (2, 72), (3, 168)])
def test_due_date_counts_calendar_hours_from_creation(priority: int, hours: int) -> None:
    assert workflow.due_for_priority(CREATED, priority) == CREATED + timedelta(hours=hours)


def test_only_an_ops_task_lets_the_requester_ask_for_approval() -> None:
    assert workflow.initial_requires_approval(ItemType.OPS_TASK, True) is True
    assert workflow.initial_requires_approval(ItemType.PAYMENT_INVESTIGATION, True) is True
    assert workflow.initial_requires_approval(ItemType.PAYMENT_INVESTIGATION, False) is True
    assert workflow.initial_requires_approval(ItemType.INCIDENT, False) is False
    with pytest.raises(ValidationFailed):
        workflow.initial_requires_approval(ItemType.INCIDENT, True)


# ----- who is needed for which field ------------------------------------------------------


def test_each_field_asks_for_its_own_permission() -> None:
    needed = workflow.required_actions(
        {
            "title": "x",
            "type": ItemType.INCIDENT,
            "priority": 1,
            "due_at": CREATED,
            "confidential": True,
            "requires_approval": False,
        }
    )

    assert set(needed) == {
        Action.EDIT_TEXT,
        Action.EDIT_TYPE,
        Action.CHANGE_PRIORITY,
        Action.EDIT_DUE_AT,
        Action.EDIT_CONFIDENTIAL,
        Action.REQUIRES_APPROVAL_OFF,
    }


def test_turning_approval_on_and_off_are_different_permissions() -> None:
    assert workflow.required_actions({"requires_approval": True}) == [Action.REQUIRES_APPROVAL_ON]
    assert workflow.required_actions({"requires_approval": False}) == [Action.REQUIRES_APPROVAL_OFF]


# ----- edits ------------------------------------------------------------------------------


def test_an_edit_that_changes_nothing_is_an_empty_plan() -> None:
    result = plan({"title": "Refund stuck", "priority": 2})

    assert not result.changed
    assert result.events == ()


def test_title_and_description_are_one_field_changed_event_naming_both() -> None:
    result = plan({"title": "New title", "description": "New text"})

    (event,) = result.events
    assert event.kind == EventKind.FIELD_CHANGED
    assert event.data == {
        "title": {"from": "Refund stuck", "to": "New title"},
        "description": {"from": "Customer charged twice.", "to": "New text"},
    }
    assert result.material_fields == ("title", "description")
    assert not event.is_decision


def test_changing_the_priority_recomputes_an_automatic_due_date_from_creation() -> None:
    result = plan({"priority": 0})

    assert result.updates["due_at"] == CREATED + timedelta(hours=4)
    (event,) = result.events
    assert event.kind == EventKind.PRIORITY_CHANGED
    assert event.data["priority"] == {"from": 2, "to": 0}
    assert event.data["due_at"]["to"] == (CREATED + timedelta(hours=4)).isoformat()


def test_a_manual_due_date_is_not_recomputed_when_the_priority_changes() -> None:
    manual = CREATED + timedelta(days=30)

    result = plan({"priority": 0}, item_due_at=manual, item_due_source=DueSource.MANUAL)

    assert "due_at" not in result.updates


def test_setting_the_due_date_makes_it_manual_for_good() -> None:
    later = CREATED + timedelta(days=5)

    result = plan({"due_at": later, "priority": 0})

    assert result.updates["due_at"] == later
    assert result.updates["due_source"] == DueSource.MANUAL
    kinds = [e.kind for e in result.events]
    assert kinds == [EventKind.FIELD_CHANGED, EventKind.PRIORITY_CHANGED]
    priority_event = result.events[1]
    assert "due_at" not in priority_event.data  # the lead's date wins over the recompute


@pytest.mark.parametrize(("old", "new"), [(0, 1), (0, 3), (1, 2), (1, 3)])
def test_lowering_from_p0_or_p1_needs_a_reason_and_is_a_decision(old: int, new: int) -> None:
    with pytest.raises(ReasonRequired):
        plan({"priority": new}, item_priority=old)

    (event,) = plan(
        {"priority": new}, item_priority=old, reason="  Customer confirmed fixed "
    ).events
    assert event.is_decision
    assert event.reason == "Customer confirmed fixed"


@pytest.mark.parametrize(("old", "new"), [(2, 3), (3, 2), (2, 0), (3, 1), (1, 0)])
def test_other_priority_changes_need_no_reason_and_are_not_decisions(old: int, new: int) -> None:
    (event,) = plan({"priority": new}, item_priority=old).events

    assert not event.is_decision
    assert event.reason is None


def test_a_blank_reason_does_not_count() -> None:
    with pytest.raises(ReasonRequired):
        plan({"priority": 3}, item_priority=1, reason="   ")


def test_turning_approval_off_needs_a_reason_and_is_a_decision() -> None:
    with pytest.raises(ReasonRequired):
        plan({"requires_approval": False}, item_requires_approval=True)

    (event,) = plan(
        {"requires_approval": False}, item_requires_approval=True, reason="Low risk"
    ).events
    assert event.kind == EventKind.REQUIRES_APPROVAL_CHANGED
    assert event.is_decision


def test_turning_approval_on_needs_no_reason_and_is_not_a_decision() -> None:
    (event,) = plan({"requires_approval": True}).events

    assert event.kind == EventKind.REQUIRES_APPROVAL_CHANGED
    assert not event.is_decision


def test_confidential_is_always_a_decision_and_its_reason_is_optional() -> None:
    (event,) = plan({"confidential": True}).events

    assert event.kind == EventKind.CONFIDENTIAL_CHANGED
    assert event.is_decision
    assert event.reason is None


def test_requires_approval_cannot_change_while_awaiting_approval() -> None:
    with pytest.raises(WorkflowViolation, match="Cancel the approval"):
        plan({"requires_approval": True}, item_status=ItemStatus.AWAITING_APPROVAL)


def test_other_fields_can_change_while_awaiting_approval() -> None:
    assert plan({"priority": 1}, item_status=ItemStatus.AWAITING_APPROVAL).changed


@pytest.mark.parametrize("field", ["title", "description", "type"])
def test_material_edits_are_refused_while_an_approval_is_on_record(field: str) -> None:
    new = ItemType.ENGINEERING if field == "type" else "something else"

    with pytest.raises(WorkflowViolation, match="approval on record"):
        plan({field: new}, has_live_approval=True)


def test_non_material_edits_are_fine_with_an_approval_on_record() -> None:
    assert plan({"priority": 3}, has_live_approval=True).changed


def test_changing_the_type_alone_keeps_the_flags() -> None:
    result = plan({"type": ItemType.PAYMENT_INVESTIGATION})

    assert "requires_approval" not in result.updates
    assert "confidential" not in result.updates


def test_changing_the_type_with_apply_defaults_resets_the_flags_and_says_so() -> None:
    result = plan(
        {"type": ItemType.COMPLIANCE_REQUEST}, apply_type_defaults=True, reason="Not needed"
    )

    assert result.updates["requires_approval"] is True
    assert result.updates["confidential"] is True
    kinds = {e.kind for e in result.events}
    assert kinds == {
        EventKind.FIELD_CHANGED,
        EventKind.CONFIDENTIAL_CHANGED,
        EventKind.REQUIRES_APPROVAL_CHANGED,
    }


def test_apply_defaults_yields_to_what_the_lead_asked_for_in_the_same_request() -> None:
    result = plan(
        {"type": ItemType.COMPLIANCE_REQUEST, "confidential": False}, apply_type_defaults=True
    )

    assert result.updates.get("confidential", False) is False


def test_going_back_to_a_default_free_type_turns_approval_off_and_needs_the_reason() -> None:
    with pytest.raises(ReasonRequired):
        plan(
            {"type": ItemType.INCIDENT},
            apply_type_defaults=True,
            item_type=ItemType.PAYMENT_INVESTIGATION,
            item_requires_approval=True,
        )


def test_event_data_is_plain_json() -> None:
    result = plan({"type": ItemType.ENGINEERING, "due_at": CREATED + timedelta(days=2)})

    (event,) = result.events
    assert event.data["type"] == {"from": "incident", "to": "engineering"}
    assert isinstance(event.data["due_at"]["to"], str)


# ----- next step (SPEC 4.5) ---------------------------------------------------------------


def step(**overrides: Any) -> workflow.NextStep:
    values: dict[str, Any] = {
        "status": ItemStatus.IN_PROGRESS,
        "priority": 2,
        "assignee_name": "Asha Rao",
        "team_name": "Payments",
        "can_approve": False,
        "sla_breached": False,
        "due_at": NOW + timedelta(days=2),
        "last_activity_at": NOW - timedelta(hours=1),
        "now": NOW,
        "viewer_is_lead": False,
        "viewer_is_requester": False,
        "blocked_reason": None,
    }
    values.update(overrides)
    return workflow.next_step(**values)


def test_next_step_awaiting_approval_for_the_one_who_can_approve() -> None:
    result = step(status=ItemStatus.AWAITING_APPROVAL, can_approve=True)

    assert (result.kind, result.label, result.severity) == (
        "approval",
        "Needs your approval",
        "high",
    )


def test_next_step_awaiting_approval_for_everyone_else_names_the_team() -> None:
    result = step(status=ItemStatus.AWAITING_APPROVAL)

    assert result.label == "Waiting for a Payments lead to approve"
    assert result.severity == "medium"


def test_next_step_awaiting_approval_beats_an_overdue_sla() -> None:
    result = step(status=ItemStatus.AWAITING_APPROVAL, sla_breached=True, can_approve=True)

    assert result.kind == "approval"


def test_next_step_overdue_says_by_how_much() -> None:
    result = step(sla_breached=True, due_at=NOW - timedelta(hours=3))

    assert (result.kind, result.label, result.severity) == ("overdue", "Overdue by 3 h", "high")


def test_next_step_a_finished_item_is_never_overdue() -> None:
    result = step(status=ItemStatus.RESOLVED, sla_breached=True, due_at=NOW - timedelta(hours=3))

    assert result.kind != "overdue"


@pytest.mark.parametrize(("priority", "severity"), [(0, "high"), (1, "high"), (2, "medium")])
def test_next_step_unowned_open_item_needs_an_owner(priority: int, severity: str) -> None:
    result = step(status=ItemStatus.NEW, assignee_name=None, priority=priority)

    assert (result.kind, result.label, result.severity) == (
        "needs_owner",
        "Needs an owner",
        severity,
    )


def test_next_step_blocked_gives_the_reason() -> None:
    result = step(status=ItemStatus.BLOCKED, blocked_reason="waiting on bank response")

    assert result.label == "Blocked: waiting on bank response"
    assert result.severity == "medium"


def test_next_step_quiet_for_72_hours_while_active() -> None:
    result = step(last_activity_at=NOW - timedelta(hours=96))

    assert (result.kind, result.label) == ("stale", "No activity for 4 days")


def test_next_step_not_stale_just_under_72_hours() -> None:
    assert step(last_activity_at=NOW - timedelta(hours=71)).kind == "in_progress"


def test_next_step_due_within_four_hours() -> None:
    result = step(due_at=NOW + timedelta(hours=2))

    assert (result.kind, result.label) == ("due_soon", "Due in 2 h")


def test_next_step_due_in_minutes_when_under_an_hour() -> None:
    assert step(due_at=NOW + timedelta(minutes=45)).label == "Due in 45 min"


def test_next_step_resolved_depends_on_who_is_looking() -> None:
    assert step(status=ItemStatus.RESOLVED, viewer_is_lead=True).label == "Resolved, ready to close"
    assert step(status=ItemStatus.RESOLVED, viewer_is_requester=True).label == "Confirm it's fixed"
    assert step(status=ItemStatus.RESOLVED).label == "Resolved"


def test_next_step_otherwise_names_the_assignee_by_first_name() -> None:
    result = step()

    assert (result.kind, result.label, result.severity) == (
        "in_progress",
        "In progress with Asha",
        "low",
    )


def test_next_step_closed() -> None:
    assert step(status=ItemStatus.CLOSED).label == "Closed"
