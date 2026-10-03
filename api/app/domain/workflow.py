"""Workflow rules (SPEC 4). Pure: no I/O, no HTTP. The one module that owns these rules.

Phase 3 holds the parts that exist before commands do: type defaults (3.1), due dates (4.2),
field edits (4.2) and the next step shown on every item (4.5). Transitions (4.1), transfer (4.3)
and approvals (4.4) join this module in phase 4.

`policy.py` decides WHO may do something. This module decides whether it is valid for THIS item
in its CURRENT state, and what the change means (which events, whether `due_at` moves).
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from app.domain.enums import DueSource, EventKind, ItemStatus, ItemType
from app.domain.errors import ReasonRequired, ValidationFailed, WorkflowViolation
from app.domain.policy import Action

# ----- constants ----------------------------------------------------------------------------

PRIORITY_DUE_HOURS = {0: 4, 1: 24, 2: 72, 3: 168}  # P0 4 h, P1 24 h, P2 3 days, P3 7 days (A7)
STALE_AFTER = timedelta(hours=72)
DUE_SOON_WITHIN = timedelta(hours=4)
OPEN_STATUSES = frozenset(
    {ItemStatus.NEW, ItemStatus.IN_PROGRESS, ItemStatus.BLOCKED, ItemStatus.AWAITING_APPROVAL}
)

# Written by the worker or by a comment: they never change `version` (SPEC 6.2).
NON_VERSIONING_EVENT_KINDS = frozenset(
    {EventKind.COMMENTED, EventKind.SLA_BREACHED, EventKind.DUPLICATE_SUGGESTED}
)

# An approval covers these (SPEC A6). `team_id` joins them when transfer arrives.
MATERIAL_FIELDS = ("title", "description", "type")


# ----- creation (SPEC 3.1) -------------------------------------------------------------------


@dataclass(frozen=True)
class TypeDefaults:
    requires_approval: bool
    confidential: bool


_TYPE_DEFAULTS = {
    ItemType.PAYMENT_INVESTIGATION: TypeDefaults(requires_approval=True, confidential=False),
    ItemType.COMPLIANCE_REQUEST: TypeDefaults(requires_approval=True, confidential=True),
}
_NO_DEFAULTS = TypeDefaults(requires_approval=False, confidential=False)


def type_defaults(item_type: ItemType) -> TypeDefaults:
    return _TYPE_DEFAULTS.get(item_type, _NO_DEFAULTS)


def due_for_priority(created_at: datetime, priority: int) -> datetime:
    """Calendar hours from creation (SPEC A7), not from now: raising the priority later does not
    restart the clock."""
    return created_at + timedelta(hours=PRIORITY_DUE_HOURS[priority])


def initial_requires_approval(item_type: ItemType, opt_in: bool) -> bool:
    """The type's default, or on when the requester asks for it. Only an ops task lets the
    requester turn approval on (SPEC 3.1); for other types it is the assignee's or a lead's call
    once the item exists."""
    default = type_defaults(item_type).requires_approval
    if opt_in and not default and item_type != ItemType.OPS_TASK:
        raise ValidationFailed(
            "Only ops tasks can ask for approval when they are raised.",
            errors=[
                {
                    "field": "requires_approval",
                    "message": "Only ops tasks can ask for approval when they are raised.",
                    "type": "value_error",
                }
            ],
        )
    return default or opt_in


# ----- field edits (SPEC 4.2) ----------------------------------------------------------------

EDITABLE_FIELDS = (
    "title",
    "description",
    "type",
    "priority",
    "due_at",
    "confidential",
    "requires_approval",
)


def required_actions(requested: Mapping[str, Any]) -> list[Action]:
    """The permissions a PATCH needs, one per field asked for (even if the value is unchanged: a
    viewer who tries to edit is refused, whatever the value). Checked against policy.py before
    anything else about the edit is judged."""
    actions: list[Action] = []
    if "title" in requested or "description" in requested:
        actions.append(Action.EDIT_TEXT)
    if "type" in requested:
        actions.append(Action.EDIT_TYPE)
    if "priority" in requested:
        actions.append(Action.CHANGE_PRIORITY)
    if "due_at" in requested:
        actions.append(Action.EDIT_DUE_AT)
    if "confidential" in requested:
        actions.append(Action.EDIT_CONFIDENTIAL)
    if "requires_approval" in requested:
        on = requested["requires_approval"]
        actions.append(Action.REQUIRES_APPROVAL_ON if on else Action.REQUIRES_APPROVAL_OFF)
    return actions


@dataclass(frozen=True)
class ItemState:
    """What an edit needs to know about the item as it is now."""

    type: ItemType
    title: str
    description: str
    priority: int
    status: ItemStatus
    due_at: datetime | None
    due_source: DueSource
    created_at: datetime
    confidential: bool
    requires_approval: bool


@dataclass(frozen=True)
class PlannedEvent:
    kind: EventKind
    data: dict[str, Any]
    reason: str | None = None
    is_decision: bool = False


@dataclass(frozen=True)
class EditPlan:
    """What a valid edit does. Empty when nothing would change (no version, no event)."""

    updates: dict[str, Any] = field(default_factory=dict)  # work_items column -> new value
    events: tuple[PlannedEvent, ...] = ()
    material_fields: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.updates)


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _from_to(old: Any, new: Any) -> dict[str, Any]:
    return {"from": _json(old), "to": _json(new)}


def plan_edit(
    item: ItemState,
    requested: Mapping[str, Any],
    *,
    apply_type_defaults: bool,
    reason: str | None,
    has_live_approval: bool,
) -> EditPlan:
    """Judge an edit that policy.py already allowed. Raises a domain error, or returns the plan.

    Nothing here does I/O; the caller writes `updates` and one event per `events` entry in one
    transaction, with a single version bump.
    """
    reason = (reason or "").strip() or None
    target: dict[str, Any] = {k: v for k, v in requested.items() if k in EDITABLE_FIELDS}
    if apply_type_defaults and "type" in target:
        defaults = type_defaults(target["type"])
        # What the lead asked for in the same request wins over the type's default.
        target.setdefault("requires_approval", defaults.requires_approval)
        target.setdefault("confidential", defaults.confidential)

    changes = {
        name: (getattr(item, name), value)
        for name, value in target.items()
        if getattr(item, name) != value
    }
    if not changes:
        return EditPlan()

    material = tuple(name for name in MATERIAL_FIELDS if name in changes)
    if material and has_live_approval:
        raise WorkflowViolation(
            "This item has an approval on record, so its title, description and type can't be "
            "edited yet."
        )
    if "requires_approval" in changes and item.status == ItemStatus.AWAITING_APPROVAL:
        raise WorkflowViolation(
            "Cancel the approval request before changing whether approval is required."
        )

    updates: dict[str, Any] = {name: new for name, (_, new) in changes.items()}
    events: list[PlannedEvent] = []

    plain = {
        name: _from_to(old, new)
        for name, (old, new) in changes.items()
        if name in ("title", "description", "type", "due_at")
    }
    if "due_at" in changes:
        updates["due_source"] = DueSource.MANUAL  # a lead's date is not recomputed any more

    if "priority" in changes:
        old_priority, new_priority = changes["priority"]
        data: dict[str, Any] = {"priority": _from_to(old_priority, new_priority)}
        lowered_from_urgent = old_priority in (0, 1) and new_priority > old_priority
        if lowered_from_urgent and reason is None:
            raise ReasonRequired("Lowering the priority from P0 or P1 needs a reason.")
        if "due_at" not in changes and item.due_source == DueSource.AUTO:
            new_due = due_for_priority(item.created_at, new_priority)
            if new_due != item.due_at:
                updates["due_at"] = new_due
                data["due_at"] = _from_to(item.due_at, new_due)
        events.append(
            PlannedEvent(EventKind.PRIORITY_CHANGED, data, reason, is_decision=lowered_from_urgent)
        )

    if plain:
        events.insert(0, PlannedEvent(EventKind.FIELD_CHANGED, plain))

    if "confidential" in changes:
        old_value, new_value = changes["confidential"]
        events.append(
            PlannedEvent(
                EventKind.CONFIDENTIAL_CHANGED,
                {"confidential": _from_to(old_value, new_value)},
                reason,
                is_decision=True,
            )
        )

    if "requires_approval" in changes:
        old_value, new_value = changes["requires_approval"]
        turned_off = new_value is False
        if turned_off and reason is None:
            raise ReasonRequired("Turning off the approval requirement needs a reason.")
        events.append(
            PlannedEvent(
                EventKind.REQUIRES_APPROVAL_CHANGED,
                {"requires_approval": _from_to(old_value, new_value)},
                reason,
                is_decision=turned_off,
            )
        )

    return EditPlan(updates, tuple(events), material)


# ----- next step (SPEC 4.5) ------------------------------------------------------------------


@dataclass(frozen=True)
class NextStep:
    kind: str
    label: str
    severity: str  # "high" | "medium" | "low"


def _span(delta: timedelta) -> str:
    minutes = max(1, round(delta.total_seconds() / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours = round(minutes / 60)
    if hours < 48:
        return f"{hours} h"
    return f"{round(hours / 24)} days"


def next_step(
    *,
    status: ItemStatus,
    priority: int,
    assignee_name: str | None,
    team_name: str,
    can_approve: bool,
    sla_breached: bool,
    due_at: datetime | None,
    last_activity_at: datetime,
    now: datetime,
    viewer_is_lead: bool,
    viewer_is_requester: bool,
    blocked_reason: str | None,
) -> NextStep:
    """The first row of SPEC 4.5 that matches."""
    is_open = status in OPEN_STATUSES
    if status == ItemStatus.AWAITING_APPROVAL:
        if can_approve:
            return NextStep("approval", "Needs your approval", "high")
        return NextStep("approval_wait", f"Waiting for a {team_name} lead to approve", "medium")
    if sla_breached and is_open:
        late = f"Overdue by {_span(now - due_at)}" if due_at and due_at < now else "Overdue"
        return NextStep("overdue", late, "high")
    if status == ItemStatus.NEW:  # an open item with no owner is always `new` (owner_when_active)
        return NextStep("needs_owner", "Needs an owner", "high" if priority <= 1 else "medium")
    if status == ItemStatus.BLOCKED:
        label = f"Blocked: {blocked_reason}" if blocked_reason else "Blocked"
        return NextStep("blocked", label, "medium")
    if status == ItemStatus.IN_PROGRESS and now - last_activity_at >= STALE_AFTER:
        return NextStep("stale", f"No activity for {_span(now - last_activity_at)}", "medium")
    if is_open and due_at is not None and now <= due_at <= now + DUE_SOON_WITHIN:
        return NextStep("due_soon", f"Due in {_span(due_at - now)}", "medium")
    if status == ItemStatus.RESOLVED:
        if viewer_is_lead:
            return NextStep("resolved", "Resolved, ready to close", "low")
        if viewer_is_requester:
            return NextStep("resolved", "Confirm it's fixed", "low")
        return NextStep("resolved", "Resolved", "low")
    if status == ItemStatus.CLOSED:
        return NextStep("closed", "Closed", "low")
    who = (assignee_name or "").split(" ")[0] or "the assignee"
    return NextStep("in_progress", f"In progress with {who}", "low")
