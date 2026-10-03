"""Replay an item's recorded history through `workflow.evaluate` (BUILD_PLAN phase 4).

The seed simulator and the real commands both write histories; this checks the simulator's output
against the code that now enforces SPEC 4: for every command in the history, `evaluate` must allow
it from the state the earlier events left, with the reason, resolution and approval it needs, and
it must lead where the history says it led. A history that `evaluate` refuses is either an
impossible history or a rule that drifted.

Who acted is not judged here: events do not record the actor's role at the time, and today's
memberships need not match it. That is policy.py's job (T-FLOW, T-VIS).
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.domain import workflow
from app.domain.enums import (
    ApprovalStatus,
    DueSource,
    EventKind,
    ItemStatus,
    ItemType,
    Resolution,
)
from app.domain.hashing import subject_hash
from app.domain.policy import Action

K = EventKind

NON_BUMPING = {K.COMMENTED, K.SLA_BREACHED, K.DUPLICATE_SUGGESTED}
EDITS = {K.FIELD_CHANGED, K.PRIORITY_CHANGED, K.CONFIDENTIAL_CHANGED, K.REQUIRES_APPROVAL_CHANGED}

APPROVAL_AFTER = {
    K.APPROVAL_REQUESTED: ApprovalStatus.PENDING,
    K.APPROVAL_APPROVED: ApprovalStatus.APPROVED,
    K.APPROVAL_REJECTED: ApprovalStatus.REJECTED,
    K.APPROVAL_CANCELLED: ApprovalStatus.CANCELLED,
    K.APPROVAL_INVALIDATED: ApprovalStatus.INVALIDATED,
}


@dataclass
class State:
    team: str | None = None
    type: str | None = None
    title: str | None = None
    description: str | None = None
    status: str | None = None
    assignee: str | None = None
    requires_approval: bool = False
    resolution: str | None = None
    duplicate_of: str | None = None
    requester: str | None = None
    approvals: dict[str, tuple[ApprovalStatus, str]] = field(default_factory=dict)

    def content_hash(self) -> str:
        assert self.team
        assert self.type
        assert self.title is not None
        assert self.description is not None
        return subject_hash(self.team, self.type, self.title, self.description)

    def facts(self) -> workflow.WorkflowFacts:
        assert self.status is not None
        statuses = [s for s, _ in self.approvals.values()]
        return workflow.WorkflowFacts(
            status=ItemStatus(self.status),
            assignee_id=uuid.UUID(self.assignee) if self.assignee else None,
            requires_approval=self.requires_approval,
            has_pending_approval=ApprovalStatus.PENDING in statuses,
            has_valid_approval=any(
                s == ApprovalStatus.APPROVED and h == self.content_hash()
                for s, h in self.approvals.values()
            ),
        )

    def approval_facts(self) -> list[workflow.ApprovalFact]:
        return [
            workflow.ApprovalFact(uuid.UUID(i), s, h, uuid.UUID(int=0))
            for i, (s, h) in self.approvals.items()
            if s in (ApprovalStatus.PENDING, ApprovalStatus.APPROVED)
        ]


def _data(event: dict[str, Any]) -> dict[str, Any]:
    raw = event["data"]
    loaded = json.loads(raw) if isinstance(raw, str) else raw
    assert isinstance(loaded, dict)
    return loaded


def _delta(data: dict[str, Any], name: str) -> Any:
    found = data.get(name)
    return found["to"] if isinstance(found, dict) and "to" in found else None


def _apply(state: State, event: dict[str, Any]) -> None:
    kind, data = K(event["kind"]), _data(event)
    for name in ("team", "type", "title", "description", "status", "assignee", "resolution"):
        if name in data:
            setattr(state, name, data[name]["to"])
    if "duplicate_of" in data:
        state.duplicate_of = data["duplicate_of"]["to"]
    if "requires_approval" in data:
        state.requires_approval = data["requires_approval"]["to"]
    if kind == K.CREATED:
        state.requester = str(event["actor_id"])
    if kind == K.APPROVAL_REQUESTED:
        state.approvals[data["approval_id"]] = (ApprovalStatus.PENDING, state.content_hash())
    elif kind in (K.APPROVAL_APPROVED, K.APPROVAL_REJECTED, K.APPROVAL_CANCELLED):
        _, content = state.approvals[data["approval_id"]]
        state.approvals[data["approval_id"]] = (APPROVAL_AFTER[kind], content)
    elif kind == K.APPROVAL_INVALIDATED:
        for approval_id in data["approval_ids"]:
            _, content = state.approvals[approval_id]
            state.approvals[approval_id] = (ApprovalStatus.INVALIDATED, content)


def _action(group: list[dict[str, Any]], state: State) -> Action | None:
    """The action a command was, from the events it wrote; None for what evaluate does not judge
    (creation, edits, comments and system notes)."""
    kinds = [K(e["kind"]) for e in group]
    first = group[0]
    data = _data(first)
    if K.TRANSFERRED in kinds:
        return Action.TRANSFER
    if K.ASSIGNED in kinds:
        acted_for_self = str(first["actor_id"]) == data["assignee"]["to"]
        return Action.CLAIM if acted_for_self and state.assignee is None else Action.ASSIGN
    if K.UNASSIGNED in kinds:
        own = str(first["actor_id"]) == state.assignee and first["reason"] is None
        return Action.RELEASE if own else Action.UNASSIGN
    if K.CLOSED in kinds:
        closing = _data(group[0])
        withdraws = (
            state.status == "new"
            and str(first["actor_id"]) == state.requester
            and _delta(closing, "resolution") == "wont_do"
        )
        return Action.WITHDRAW if withdraws else Action.CLOSE
    single = {
        K.BLOCKED: Action.BLOCK,
        K.UNBLOCKED: Action.UNBLOCK,
        K.APPROVAL_REQUESTED: Action.REQUEST_APPROVAL,
        K.APPROVAL_APPROVED: Action.APPROVE,
        K.APPROVAL_REJECTED: Action.REJECT,
        K.APPROVAL_CANCELLED: Action.CANCEL_APPROVAL,
        K.RESOLVED: Action.RESOLVE,
        K.REOPENED: Action.REOPEN,
    }
    for kind in kinds:
        if kind in single:
            return single[kind]
    return None


def replay_item(events: list[dict[str, Any]], key: str = "?") -> list[str]:
    """Problems found replaying one item's events (oldest first). Empty means it replays."""
    problems: list[str] = []
    state = State()
    groups: list[list[dict[str, Any]]] = []
    for event in events:
        if (
            groups
            and K(event["kind"]) not in NON_BUMPING
            and (
                K(groups[-1][0]["kind"]) not in NON_BUMPING
                and groups[-1][0]["item_version"] == event["item_version"]
            )
        ):
            groups[-1].append(event)
        else:
            groups.append([event])

    for group in groups:
        first = group[0]
        if K(first["kind"]) == K.CREATED:
            for event in group:
                _apply(state, event)
            continue
        action = _action(group, state)
        if action is not None:
            problems += _judge(key, action, group, state)
        if any(K(e["kind"]) in EDITS for e in group):
            problems += _judge_edit_invalidation(key, group, state)
        for event in group:
            _apply(state, event)
    return problems


def _judge(key: str, action: Action, group: list[dict[str, Any]], state: State) -> list[str]:
    driver = next(e for e in group if K(e["kind"]) != K.APPROVAL_CANCELLED or len(group) == 1)
    data = _data(driver)
    resolution = _delta(data, "resolution")
    duplicate = _delta(data, "duplicate_of")
    payload = workflow.Payload(
        reason=driver["reason"] if action != Action.UNASSIGN else None,
        resolution=Resolution(resolution) if resolution else None,
        duplicate_of=uuid.UUID(duplicate) if duplicate else None,
        new_assignee_can_work=True,
    )
    if action == Action.CLOSE and state.status == "resolved":
        payload = workflow.Payload(reason=driver["reason"])
    verdict = workflow.evaluate(action, state.facts(), payload)
    where = f"{key} event {driver['id']} ({driver['kind']}, v{driver['item_version']})"
    if isinstance(verdict, workflow.Violation):
        return [f"{where}: {action.value} refused from {state.status}: {verdict.message}"]
    recorded = _delta(_data(driver), "status")
    if recorded is None:  # the status delta may sit on a second event of the command
        recorded = next((_delta(_data(e), "status") for e in group if "status" in _data(e)), None)
    expected = verdict.to_status.value if verdict.to_status else None
    stays_blocked = action == Action.ASSIGN and state.status == "blocked"
    if expected is not None and recorded is not None and recorded != expected and not stays_blocked:
        return [f"{where}: table says {state.status} -> {expected}, history says {recorded}"]
    return []


def _judge_edit_invalidation(key: str, group: list[dict[str, Any]], state: State) -> list[str]:
    """For an edit of the title, description or type, `plan_edit` must invalidate exactly the
    approvals the history says it did."""
    changes: dict[str, Any] = {}
    for event in group:
        data = _data(event)
        for name in ("title", "description", "type"):
            if name in data and K(event["kind"]) == K.FIELD_CHANGED:
                changes[name] = ItemType(data[name]["to"]) if name == "type" else data[name]["to"]
    if not changes or state.status is None:
        return []
    assert state.team
    assert state.type
    assert state.title is not None
    assert state.description is not None
    item = workflow.ItemState(
        team_id=uuid.UUID(state.team),
        type=ItemType(state.type),
        title=state.title,
        description=state.description,
        priority=0,
        status=ItemStatus(state.status),
        due_at=None,
        due_source=DueSource.MANUAL,
        created_at=datetime.min.replace(tzinfo=UTC),
        confidential=False,
        requires_approval=state.requires_approval,
    )
    plan = workflow.plan_edit(
        item, changes, apply_type_defaults=False, reason=None, approvals=state.approval_facts()
    )
    recorded = sorted(
        a for e in group if K(e["kind"]) == K.APPROVAL_INVALIDATED for a in _data(e)["approval_ids"]
    )
    mine = sorted(str(a) for a in plan.invalidated)
    if recorded != mine:
        return [
            f"{key}: the history invalidated {recorded} for {sorted(changes)}, plan_edit {mine}"
        ]
    return []
