"""Workflow rules (SPEC 4). Pure: no I/O, no HTTP. The one module that owns these rules.

It holds type defaults (3.1), due dates (4.2), field edits (4.2), the transition table (4.1),
transfer (4.3), approval binding (4.4) and the next step shown on every item (4.5).

`policy.py` decides WHO may do something. This module decides whether it is valid for THIS item
in its CURRENT state, and what the change means (which fields, which events, whether `due_at`
moves). `allowed_actions` is the intersection of the two, so the UI never re-implements a rule.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from app.domain.enums import (
    ApprovalStatus,
    DueSource,
    EventKind,
    ItemStatus,
    ItemType,
    Resolution,
)
from app.domain.errors import (
    ALL_ERRORS,
    DomainError,
    ReasonRequired,
    ValidationFailed,
    WorkflowViolation,
)
from app.domain.hashing import subject_hash
from app.domain.policy import Action, ActorContext, can
from app.domain.policy import ItemFacts as PolicyFacts

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

# An approval covers these (SPEC A6, 4.4), and the team: a transfer changes `team_id`, which is
# the fourth member of the hash (domain/hashing.py).
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


# ----- events a command plans ----------------------------------------------------------------


@dataclass(frozen=True)
class PlannedEvent:
    """One event a command will write through record_event(), in order."""

    kind: EventKind
    data: dict[str, Any]
    reason: str | None = None
    is_decision: bool = False


def _json(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def _from_to(old: Any, new: Any) -> dict[str, Any]:
    return {"from": _json(old), "to": _json(new)}


# ----- approval binding (SPEC 4.4) -----------------------------------------------------------


@dataclass(frozen=True)
class ApprovalFact:
    """A pending or approved approval of the item, with the content hash it covers."""

    id: uuid.UUID
    status: ApprovalStatus
    subject_hash: str
    requested_by: uuid.UUID


def has_valid_approval(approvals: Iterable[ApprovalFact], content_hash: str) -> bool:
    """An `approved` approval that covers the content the item has now (resolve needs one)."""
    return any(
        a.status == ApprovalStatus.APPROVED and a.subject_hash == content_hash for a in approvals
    )


def stale_approvals(approvals: Iterable[ApprovalFact], new_hash: str) -> list[ApprovalFact]:
    """What a change to the material fields stops covering: every pending approval, and every
    approved one whose hash is not the new content's (decision 37: all of them, not only the
    latest, so an older approval can never match again after an edit back)."""
    return [
        a
        for a in approvals
        if a.status == ApprovalStatus.PENDING
        or (a.status == ApprovalStatus.APPROVED and a.subject_hash != new_hash)
    ]


def invalidation_event(
    stale: Sequence[ApprovalFact], fields: Sequence[str], status: ItemStatus
) -> tuple[PlannedEvent, str]:
    """The `approval_invalidated` event for `stale`, and the reason stored on the approvals. A
    pending approval takes the item from awaiting_approval back to in_progress."""
    reason = f"Content changed: {', '.join(fields)}"
    data: dict[str, Any] = {"fields": list(fields), "approval_ids": [str(a.id) for a in stale]}
    if any(a.status == ApprovalStatus.PENDING for a in stale) and (
        status == ItemStatus.AWAITING_APPROVAL
    ):
        data["status"] = _from_to(ItemStatus.AWAITING_APPROVAL, ItemStatus.IN_PROGRESS)
    return PlannedEvent(EventKind.APPROVAL_INVALIDATED, data, reason, is_decision=True), reason


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

    team_id: uuid.UUID
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
class EditPlan:
    """What a valid edit does. Empty when nothing would change (no version, no event)."""

    updates: dict[str, Any] = field(default_factory=dict)  # work_items column -> new value
    events: tuple[PlannedEvent, ...] = ()
    material_fields: tuple[str, ...] = ()
    # Approvals the edit stops covering (SPEC 4.4): the caller marks them invalidated, with
    # `invalidated_reason`, in the same transaction.
    invalidated: tuple[uuid.UUID, ...] = ()
    invalidated_reason: str | None = None

    @property
    def changed(self) -> bool:
        return bool(self.updates)


def plan_edit(
    item: ItemState,
    requested: Mapping[str, Any],
    *,
    apply_type_defaults: bool,
    reason: str | None,
    approvals: Sequence[ApprovalFact] = (),
) -> EditPlan:
    """Judge an edit that policy.py already allowed. Raises a domain error, or returns the plan.

    Nothing here does I/O; the caller writes `updates` and one event per `events` entry in one
    transaction, with a single version bump. A change to a material field also invalidates the
    approvals that no longer cover the content (SPEC 4.4), in that same plan.
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

    invalidated: tuple[uuid.UUID, ...] = ()
    invalidated_reason: str | None = None
    if material:
        new_hash = subject_hash(
            item.team_id,
            _json(updates.get("type", item.type)),
            updates.get("title", item.title),
            updates.get("description", item.description),
        )
        stale = stale_approvals(approvals, new_hash)
        if stale:
            event, invalidated_reason = invalidation_event(stale, material, item.status)
            if any(a.status == ApprovalStatus.PENDING for a in stale):
                updates["status"] = ItemStatus.IN_PROGRESS  # back to the owner, who may ask again
            events.append(event)
            invalidated = tuple(a.id for a in stale)

    return EditPlan(updates, tuple(events), material, invalidated, invalidated_reason)


# ----- transitions (SPEC 4.1) ----------------------------------------------------------------

S = ItemStatus


@dataclass(frozen=True)
class Row:
    """One row of the SPEC 4.1 table: what an action does from one starting status."""

    to: ItemStatus | None = None  # None: the command works it out (reopen, transfer)
    reason: bool = False  # a reason (for resolve, the resolution note) is required
    resolution: bool = False  # the caller must name a resolution


# One row per (action, starting status). An action that has no row for a status is not allowed
# from it. This is the whole table; nothing else in the code decides whether a transition is valid.
TRANSITIONS: dict[tuple[Action, ItemStatus], Row] = {
    (Action.CLAIM, S.NEW): Row(S.IN_PROGRESS),
    (Action.RELEASE, S.IN_PROGRESS): Row(S.NEW),
    (Action.RELEASE, S.BLOCKED): Row(S.NEW),
    (Action.ASSIGN, S.NEW): Row(S.IN_PROGRESS),
    (Action.ASSIGN, S.IN_PROGRESS): Row(S.IN_PROGRESS),
    (Action.ASSIGN, S.BLOCKED): Row(S.BLOCKED),
    (Action.UNASSIGN, S.IN_PROGRESS): Row(S.NEW),
    (Action.UNASSIGN, S.BLOCKED): Row(S.NEW),
    (Action.BLOCK, S.IN_PROGRESS): Row(S.BLOCKED, reason=True),
    (Action.UNBLOCK, S.BLOCKED): Row(S.IN_PROGRESS),
    (Action.REQUEST_APPROVAL, S.IN_PROGRESS): Row(S.AWAITING_APPROVAL),
    (Action.APPROVE, S.AWAITING_APPROVAL): Row(S.IN_PROGRESS),
    (Action.REJECT, S.AWAITING_APPROVAL): Row(S.IN_PROGRESS, reason=True),
    (Action.CANCEL_APPROVAL, S.AWAITING_APPROVAL): Row(S.IN_PROGRESS),
    (Action.RESOLVE, S.IN_PROGRESS): Row(S.RESOLVED, reason=True),
    (Action.REOPEN, S.RESOLVED): Row(None, reason=True),
    (Action.REOPEN, S.CLOSED): Row(None, reason=True),
    (Action.CLOSE, S.NEW): Row(S.CLOSED, reason=True, resolution=True),
    (Action.CLOSE, S.IN_PROGRESS): Row(S.CLOSED, reason=True, resolution=True),
    (Action.CLOSE, S.BLOCKED): Row(S.CLOSED, reason=True, resolution=True),
    (Action.CLOSE, S.RESOLVED): Row(S.CLOSED),
    (Action.WITHDRAW, S.NEW): Row(S.CLOSED, reason=True),
    (Action.TRANSFER, S.NEW): Row(None, reason=True),
    (Action.TRANSFER, S.IN_PROGRESS): Row(None, reason=True),
    (Action.TRANSFER, S.BLOCKED): Row(None, reason=True),
    (Action.TRANSFER, S.AWAITING_APPROVAL): Row(None, reason=True),
}
TRANSITION_ACTIONS = frozenset(action for action, _ in TRANSITIONS)

_STATUS_WORDS = {
    ItemStatus.NEW: "new",
    ItemStatus.IN_PROGRESS: "in progress",
    ItemStatus.BLOCKED: "blocked",
    ItemStatus.AWAITING_APPROVAL: "awaiting approval",
    ItemStatus.RESOLVED: "resolved",
    ItemStatus.CLOSED: "closed",
}
_PHRASE = {
    Action.CLAIM: "claim",
    Action.RELEASE: "release",
    Action.ASSIGN: "assign",
    Action.UNASSIGN: "unassign",
    Action.BLOCK: "block",
    Action.UNBLOCK: "unblock",
    Action.REQUEST_APPROVAL: "request approval for",
    Action.APPROVE: "approve",
    Action.REJECT: "reject",
    Action.CANCEL_APPROVAL: "cancel the approval of",
    Action.RESOLVE: "resolve",
    Action.REOPEN: "reopen",
    Action.CLOSE: "close",
    Action.WITHDRAW: "withdraw",
    Action.TRANSFER: "transfer",
}
# SPEC 4.1: these three are refused while an approval is waiting; cancel it first.
_NEEDS_APPROVAL_CANCELLED = frozenset({Action.RELEASE, Action.ASSIGN, Action.UNASSIGN})


@dataclass(frozen=True)
class WorkflowFacts:
    """What the transition table needs to know about an item right now."""

    status: ItemStatus
    assignee_id: uuid.UUID | None
    requires_approval: bool
    has_pending_approval: bool
    has_valid_approval: bool  # an approved approval whose hash matches the current content


@dataclass(frozen=True)
class Payload:
    """What the caller supplied with the command (already parsed, not yet judged)."""

    reason: str | None = None
    resolution: Resolution | None = None
    duplicate_of: uuid.UUID | None = None
    new_assignee_can_work: bool | None = None  # assign: is the target a member or lead here?


@dataclass(frozen=True)
class Allowed:
    to_status: ItemStatus | None  # None for reopen and transfer, which work it out
    resolution: Resolution | None = None
    duplicate_of: uuid.UUID | None = None
    reason: str | None = None  # trimmed; None when blank


@dataclass(frozen=True)
class Violation:
    code: str  # a SPEC 12 code
    message: str
    field: str | None = None

    def error(self) -> DomainError:
        cls = next(e for e in ALL_ERRORS if e.code == self.code)
        errors = None
        if self.field is not None:
            errors = [{"field": self.field, "message": self.message, "type": "value_error"}]
        return cls(self.message, errors=errors)


def pending_conflict(action: Action, facts: WorkflowFacts) -> Violation | None:
    """The one workflow answer that is given BEFORE the version is compared (decision 35): a
    second approval request while one is waiting is 409, not a stale-version 412."""
    if action == Action.REQUEST_APPROVAL and facts.has_pending_approval:
        return Violation(
            "APPROVAL_ALREADY_PENDING",
            "This item already has an approval request waiting for a decision.",
        )
    return None


def _not_from(action: Action, status: ItemStatus) -> Violation:
    if status == ItemStatus.AWAITING_APPROVAL and action in _NEEDS_APPROVAL_CANCELLED:
        return Violation(
            "WORKFLOW_VIOLATION",
            f"You can't {_PHRASE[action]} an item that is awaiting approval. Cancel the "
            "approval request first.",
        )
    return Violation(
        "WORKFLOW_VIOLATION",
        f"You can't {_PHRASE[action]} an item that is {_STATUS_WORDS[status]}.",
    )


def evaluate(action: Action, facts: WorkflowFacts, payload: Payload) -> Allowed | Violation:
    """Is `action` valid for an item in this state, with this payload? Judges the item and the
    input only; who may do it is policy.py. The first rule that fails is returned."""
    conflict = pending_conflict(action, facts)
    if conflict is not None:
        return conflict
    row = TRANSITIONS.get((action, facts.status))
    if row is None:
        return _not_from(action, facts.status)
    if action == Action.CLAIM and facts.assignee_id is not None:
        return Violation("ALREADY_CLAIMED", "Someone already owns this item.")
    if action in (Action.APPROVE, Action.REJECT, Action.CANCEL_APPROVAL) and (
        not facts.has_pending_approval
    ):
        return Violation("WORKFLOW_VIOLATION", "There is no approval request waiting.")
    if action == Action.ASSIGN and payload.new_assignee_can_work is False:
        return Violation(
            "WORKFLOW_VIOLATION", "Only members and leads of the team can own its items."
        )
    if action == Action.RESOLVE and facts.requires_approval and not facts.has_valid_approval:
        return Violation(
            "APPROVAL_REQUIRED",
            "This item needs an approved request that covers its current content before it can "
            "be resolved. Request approval first.",
        )

    reason = (payload.reason or "").strip() or None
    if row.reason and reason is None:
        return Violation("REASON_REQUIRED", _reason_message(action), field="reason")

    resolution, duplicate_of = payload.resolution, payload.duplicate_of
    if action == Action.RESOLVE:
        resolution = resolution or Resolution.DONE
    elif action == Action.WITHDRAW:
        resolution, duplicate_of = Resolution.WONT_DO, None
    elif row.resolution:
        if resolution is None:
            return Violation("VALIDATION_FAILED", "Choose a resolution.", field="resolution")
    else:
        resolution, duplicate_of = None, None  # not asked for here: the item keeps its own
    if action in (Action.RESOLVE, Action.CLOSE) and (row.resolution or action == Action.RESOLVE):
        if resolution == Resolution.DUPLICATE and duplicate_of is None:
            return Violation(
                "VALIDATION_FAILED",
                "Name the item this one duplicates.",
                field="duplicate_of",
            )
        if resolution != Resolution.DUPLICATE and duplicate_of is not None:
            return Violation(
                "VALIDATION_FAILED",
                "duplicate_of only goes with the resolution 'duplicate'.",
                field="duplicate_of",
            )
    return Allowed(row.to, resolution, duplicate_of, reason)


def _reason_message(action: Action) -> str:
    return {
        Action.BLOCK: "Say what the item is waiting on.",
        Action.REJECT: "A rejection needs a reason.",
        Action.RESOLVE: "Add a note saying what was done.",
        Action.REOPEN: "Say why it is being reopened.",
        Action.CLOSE: "Say why it is being closed.",
        Action.WITHDRAW: "Say why you are withdrawing it.",
        Action.TRANSFER: "Say why it is moving to another team.",
    }[action]


def available(action: Action, facts: WorkflowFacts) -> bool:
    """Is `action` worth offering for an item in this state? Ignores the payload (a close still
    needs a resolution) and the approval gate of resolve: the UI offers Resolve and the server
    answers APPROVAL_REQUIRED, which points at Request approval (SPEC 12)."""
    awaiting = facts.status == ItemStatus.AWAITING_APPROVAL
    if action == Action.REQUIRES_APPROVAL_ON:
        return not facts.requires_approval and not awaiting
    if action == Action.REQUIRES_APPROVAL_OFF:
        return facts.requires_approval and not awaiting
    if action not in TRANSITION_ACTIONS:
        return True  # comment, watch and the field edits depend on policy alone
    if (action, facts.status) not in TRANSITIONS:
        return False
    if action == Action.CLAIM:
        return facts.assignee_id is None
    if action == Action.REQUEST_APPROVAL:
        return not facts.has_pending_approval
    if action in (Action.APPROVE, Action.REJECT, Action.CANCEL_APPROVAL):
        return facts.has_pending_approval
    return True


# What a person may be offered, in the order the UI lists it. VIEW is implied.
OFFERED_ACTIONS: tuple[Action, ...] = (
    Action.COMMENT,
    Action.WATCH,
    Action.CLAIM,
    Action.RELEASE,
    Action.ASSIGN,
    Action.UNASSIGN,
    Action.BLOCK,
    Action.UNBLOCK,
    Action.REQUEST_APPROVAL,
    Action.APPROVE,
    Action.REJECT,
    Action.CANCEL_APPROVAL,
    Action.RESOLVE,
    Action.REOPEN,
    Action.CLOSE,
    Action.WITHDRAW,
    Action.TRANSFER,
    Action.EDIT_TEXT,
    Action.CHANGE_PRIORITY,
    Action.EDIT_TYPE,
    Action.EDIT_DUE_AT,
    Action.EDIT_CONFIDENTIAL,
    Action.REQUIRES_APPROVAL_ON,
    Action.REQUIRES_APPROVAL_OFF,
)


def allowed_actions(ctx: ActorContext, item: PolicyFacts, facts: WorkflowFacts) -> list[Action]:
    """policy ∩ workflow: what this person may do to this item in its current state."""
    return [
        action
        for action in OFFERED_ACTIONS
        if can(ctx, action, item).allowed and available(action, facts)
    ]


# ----- what a valid command changes ----------------------------------------------------------


@dataclass(frozen=True)
class TransitionState:
    """The fields a transition reads and rewrites."""

    status: ItemStatus
    assignee_id: uuid.UUID | None
    resolution: Resolution | None = None
    duplicate_of_id: uuid.UUID | None = None


@dataclass(frozen=True)
class TransitionPlan:
    updates: dict[str, Any]  # work_items column -> new value
    events: tuple[PlannedEvent, ...]


def _status_event(old: ItemStatus, new: ItemStatus) -> PlannedEvent:
    return PlannedEvent(EventKind.STATUS_CHANGED, {"status": _from_to(old, new)})


def plan_transition(
    action: Action,
    state: TransitionState,
    allowed: Allowed,
    *,
    actor_id: uuid.UUID,
    now: datetime,
    new_assignee_id: uuid.UUID | None = None,
    approval_id: uuid.UUID | None = None,
    previous_owner_can_work: bool = False,
    note: str | None = None,
) -> TransitionPlan:
    """The columns and events of a command `evaluate` allowed (never called for transfer, which
    has its own plan). `note` is an optional approve/close note; `allowed.reason` is the trimmed
    required one."""
    old = state.status
    to = allowed.to_status
    reason = allowed.reason
    status_delta = {"status": _from_to(old, to)} if to is not None else {}
    approval = {"approval_id": str(approval_id)} if approval_id else {}

    match action:
        case Action.CLAIM:
            assert to is not None
            return TransitionPlan(
                {"assignee_id": actor_id, "status": to},
                (
                    PlannedEvent(EventKind.ASSIGNED, {"assignee": _from_to(None, actor_id)}),
                    _status_event(old, to),
                ),
            )
        case Action.ASSIGN:
            assert to is not None
            assert new_assignee_id is not None
            events = [
                PlannedEvent(
                    EventKind.ASSIGNED, {"assignee": _from_to(state.assignee_id, new_assignee_id)}
                )
            ]
            if old != to:
                events.append(_status_event(old, to))
            return TransitionPlan({"assignee_id": new_assignee_id, "status": to}, tuple(events))
        case Action.RELEASE | Action.UNASSIGN:
            assert to is not None
            return TransitionPlan(
                {"assignee_id": None, "status": to},
                (
                    PlannedEvent(
                        EventKind.UNASSIGNED,
                        {"assignee": _from_to(state.assignee_id, None)},
                        reason,
                    ),
                    _status_event(old, to),
                ),
            )
        case Action.BLOCK:
            assert to is not None
            return TransitionPlan(
                {"status": to}, (PlannedEvent(EventKind.BLOCKED, status_delta, reason),)
            )
        case Action.UNBLOCK:
            assert to is not None
            return TransitionPlan(
                {"status": to}, (PlannedEvent(EventKind.UNBLOCKED, status_delta),)
            )
        case Action.REQUEST_APPROVAL:
            assert to is not None
            return TransitionPlan(
                {"status": to},
                (PlannedEvent(EventKind.APPROVAL_REQUESTED, {**approval, **status_delta}, note),),
            )
        case Action.APPROVE | Action.REJECT:
            assert to is not None
            approved = action == Action.APPROVE
            kind = EventKind.APPROVAL_APPROVED if approved else EventKind.APPROVAL_REJECTED
            # Decision events always carry a reason (decision 36): the note, or a fixed text.
            why = reason or (note or "").strip() or ("Approved" if approved else None)
            return TransitionPlan(
                {"status": to},
                (PlannedEvent(kind, {**approval, **status_delta}, why, is_decision=True),),
            )
        case Action.CANCEL_APPROVAL:
            assert to is not None
            return TransitionPlan(
                {"status": to},
                (PlannedEvent(EventKind.APPROVAL_CANCELLED, {**approval, **status_delta}),),
            )
        case Action.RESOLVE:
            assert to is not None
            data = {
                **status_delta,
                "resolution": _from_to(state.resolution, allowed.resolution),
            }
            if allowed.duplicate_of is not None:
                data["duplicate_of"] = _from_to(state.duplicate_of_id, allowed.duplicate_of)
            return TransitionPlan(
                {
                    "status": to,
                    "resolution": allowed.resolution,
                    "resolution_note": reason,
                    "duplicate_of_id": allowed.duplicate_of,
                    "resolved_at": now,
                },
                (PlannedEvent(EventKind.RESOLVED, data, reason, is_decision=True),),
            )
        case Action.CLOSE | Action.WITHDRAW:
            assert to is not None
            if old == ItemStatus.RESOLVED:  # it already has its resolution
                why = (note or "").strip() or "Closed after resolution"
                return TransitionPlan(
                    {"status": to, "closed_at": now},
                    (PlannedEvent(EventKind.CLOSED, status_delta, why, is_decision=True),),
                )
            data = {**status_delta, "resolution": _from_to(state.resolution, allowed.resolution)}
            if allowed.duplicate_of is not None:
                data["duplicate_of"] = _from_to(state.duplicate_of_id, allowed.duplicate_of)
            return TransitionPlan(
                {
                    "status": to,
                    "resolution": allowed.resolution,
                    "resolution_note": reason,
                    "duplicate_of_id": allowed.duplicate_of,
                    "closed_at": now,
                },
                (PlannedEvent(EventKind.CLOSED, data, reason, is_decision=True),),
            )
        case Action.REOPEN:
            return _plan_reopen(state, reason, previous_owner_can_work)
    raise ValueError(f"plan_transition does not handle {action}")


def _plan_reopen(
    state: TransitionState, reason: str | None, previous_owner_can_work: bool
) -> TransitionPlan:
    """Back to in_progress with the previous owner if they can still work it; otherwise to new,
    unassigned, because `owner_when_active` forbids in_progress without an owner (SPEC 4.1)."""
    keeps_owner = state.assignee_id is not None and previous_owner_can_work
    to = ItemStatus.IN_PROGRESS if keeps_owner else ItemStatus.NEW
    data: dict[str, Any] = {"status": _from_to(state.status, to)}
    updates: dict[str, Any] = {
        "status": to,
        "resolution": None,
        "resolution_note": None,
        "duplicate_of_id": None,
        "resolved_at": None,
        "closed_at": None,
    }
    if state.assignee_id is not None and not keeps_owner:
        data["assignee"] = _from_to(state.assignee_id, None)
        updates["assignee_id"] = None
    if state.resolution is not None:
        data["resolution"] = _from_to(state.resolution, None)
    if state.duplicate_of_id is not None:
        data["duplicate_of"] = _from_to(state.duplicate_of_id, None)
    return TransitionPlan(updates, (PlannedEvent(EventKind.REOPENED, data, reason, True),))


# ----- transfer (SPEC 4.3) -------------------------------------------------------------------


@dataclass(frozen=True)
class TransferPlan:
    updates: dict[str, Any]
    events: tuple[PlannedEvent, ...]
    cancelled: uuid.UUID | None  # the pending approval, cancelled with the reason "transferred"
    invalidated: tuple[uuid.UUID, ...]  # approved approvals that covered the old team
    invalidated_reason: str | None


def plan_transfer(
    state: TransitionState,
    *,
    from_team_id: uuid.UUID,
    to_team_id: uuid.UUID,
    assignee_can_work_there: bool,
    approvals: Sequence[ApprovalFact],
    reason: str,
) -> TransferPlan:
    """The key stays, the team changes (4.3.1). An owner who cannot work in the new team is
    unassigned and the item goes back to `new` (4.3.2); a pending approval is cancelled and an
    approved one invalidated, because the team is part of what was approved (4.3.3)."""
    status = state.status
    events: list[PlannedEvent] = []
    updates: dict[str, Any] = {"team_id": to_team_id}

    pending = next((a for a in approvals if a.status == ApprovalStatus.PENDING), None)
    if pending is not None:
        events.append(
            PlannedEvent(
                EventKind.APPROVAL_CANCELLED,
                {
                    "approval_id": str(pending.id),
                    "status": _from_to(status, ItemStatus.IN_PROGRESS),
                },
                "transferred",
            )
        )
        status = ItemStatus.IN_PROGRESS

    data: dict[str, Any] = {"team": _from_to(from_team_id, to_team_id)}
    if state.assignee_id is not None and not assignee_can_work_there:
        data["assignee"] = _from_to(state.assignee_id, None)
        updates["assignee_id"] = None
        if status != ItemStatus.NEW:
            data["status"] = _from_to(status, ItemStatus.NEW)
            status = ItemStatus.NEW
    if status != state.status:
        updates["status"] = status
    events.append(PlannedEvent(EventKind.TRANSFERRED, data, reason, is_decision=True))

    stale = [a for a in approvals if a.status == ApprovalStatus.APPROVED]
    invalidated_reason: str | None = None
    if stale:
        event, invalidated_reason = invalidation_event(stale, ["team"], status)
        events.append(event)
    return TransferPlan(
        updates,
        tuple(events),
        pending.id if pending else None,
        tuple(a.id for a in stale),
        invalidated_reason,
    )


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
