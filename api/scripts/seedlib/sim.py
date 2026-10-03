"""One work item's history, built by walking SPEC 4.1 transition by transition.

ItemSim holds the item's state and exposes one method per command (claim, resolve, transfer...).
Each method checks the transition table first, changes state, and records the events that the
real command would write: one version per command, every changed field as {from, to}. Because
nothing else touches the state, an impossible history cannot be produced.

The runner at the bottom steers an item toward a target outcome with a little random noise
(comments, priority changes, hand-overs) and stops when the clock would pass "now".
"""

import random
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import ApprovalStatus, DueSource, EventKind, ItemStatus, Resolution
from app.domain.hashing import subject_hash

from . import text

S = ItemStatus
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)  # placeholder until the command is sealed
OPEN = {S.NEW, S.IN_PROGRESS, S.BLOCKED, S.AWAITING_APPROVAL}
SLA_HOURS = {0: 4, 1: 24, 2: 72, 3: 168}  # SPEC A7: calendar hours from creation

# SPEC 4.1: the statuses each action may start from.
TRANSITIONS: dict[str, set[ItemStatus]] = {
    "claim": {S.NEW},
    "assign": {S.NEW, S.IN_PROGRESS, S.BLOCKED},
    "release": {S.IN_PROGRESS, S.BLOCKED},
    "unassign": {S.IN_PROGRESS, S.BLOCKED},
    "block": {S.IN_PROGRESS},
    "unblock": {S.BLOCKED},
    "request_approval": {S.IN_PROGRESS},
    "decide": {S.AWAITING_APPROVAL},
    "cancel_approval": {S.AWAITING_APPROVAL},
    "resolve": {S.IN_PROGRESS},
    "reopen": {S.RESOLVED, S.CLOSED},
    "close": {S.NEW, S.IN_PROGRESS, S.BLOCKED, S.RESOLVED},
    "withdraw": {S.NEW},
    "transfer": OPEN,
    "priority": OPEN,
    "edit": OPEN,
    "set_due": OPEN,
    "confidential": OPEN,
    "requires_approval": {S.NEW, S.IN_PROGRESS, S.BLOCKED},
}


class InvalidTransition(Exception):
    pass


def ser(value: Any) -> Any:
    """JSON-friendly form of a field value, for event data."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def change(old: Any, new: Any) -> dict[str, Any]:
    return {"from": ser(old), "to": ser(new)}


@dataclass
class TeamCtx:
    id: uuid.UUID
    key: str
    name: str
    leads: list[uuid.UUID] = field(default_factory=list)
    members: list[uuid.UUID] = field(default_factory=list)
    viewers: list[uuid.UUID] = field(default_factory=list)
    next_number: int = 0
    worker_set: set[uuid.UUID] = field(default_factory=set)
    worker_pool: list[uuid.UUID] = field(default_factory=list)  # members weigh 3x leads

    def finish(self) -> None:
        self.worker_set = set(self.leads) | set(self.members)
        self.worker_pool = [*self.members, *self.members, *self.members, *self.leads]


@dataclass
class EventRec:
    kind: EventKind
    actor: uuid.UUID | None
    team_id: uuid.UUID
    data: dict[str, Any]
    reason: str | None
    is_decision: bool
    version: int = 0
    at: datetime = EPOCH


@dataclass
class ApprovalRec:
    id: uuid.UUID
    requested_by: uuid.UUID
    requested_at: datetime
    request_note: str | None
    subject_hash: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_by: uuid.UUID | None = None
    decided_at: datetime | None = None
    decision_note: str | None = None
    invalidated_reason: str | None = None


@dataclass
class CommentRec:
    author: uuid.UUID
    body: str
    at: datetime


class ItemSim:
    def __init__(
        self,
        rng: random.Random,
        *,
        team: TeamCtx,
        item_type: str,
        title: str,
        description: str,
        priority: int,
        requester: uuid.UUID,
        confidential: bool,
        requires_approval: bool,
        created_at: datetime,
    ) -> None:
        self.rng = rng
        self.id = uuid.UUID(int=rng.getrandbits(128), version=4)
        self.team = team
        self.origin = team
        team.next_number += 1
        self.number = team.next_number
        self.key = f"{team.key}-{self.number}"
        self.type = item_type
        self.title = title
        self.description = description
        self.priority = priority
        self.requester = requester
        self.confidential = confidential
        self.requires_approval = requires_approval
        self.created_at = created_at

        self.status: ItemStatus = S.NEW
        self.assignee: uuid.UUID | None = None
        self.resolution: Resolution | None = None
        self.resolution_note: str | None = None
        self.duplicate_of: uuid.UUID | None = None
        self.due_source = DueSource.AUTO
        self.due_at: datetime | None = created_at + timedelta(hours=SLA_HOURS[priority])
        self.sla_breached_at: datetime | None = None
        self.resolved_at: datetime | None = None
        self.closed_at: datetime | None = None
        self.version = 0
        self.updated_at = created_at
        self.last_activity_at = created_at
        self.last_time = created_at
        self.reopen_count = 0

        self.events: list[EventRec] = []
        self.comments: list[CommentRec] = []
        self.approvals: list[ApprovalRec] = []
        self.watchers: set[uuid.UUID] = {requester}
        self.similar: list[tuple[uuid.UUID, float]] = []
        self._pending: list[EventRec] = []

        data = {
            "team": change(None, team.id),
            "type": change(None, item_type),
            "title": change(None, title),
            "description": change(None, description),
            "priority": change(None, priority),
            "status": change(None, S.NEW),
            "confidential": change(None, confidential),
            "requires_approval": change(None, requires_approval),
            "due_at": change(None, self.due_at),
            "due_source": change(None, DueSource.AUTO),
        }
        self._emit(EventKind.CREATED, requester, data)
        self._seal(created_at)

    # ----- plumbing -------------------------------------------------------------------

    def _emit(
        self,
        kind: EventKind,
        actor: uuid.UUID | None,
        data: dict[str, Any],
        reason: str | None = None,
        decision: bool = False,
    ) -> None:
        self._pending.append(EventRec(kind, actor, self.team.id, data, reason, decision))

    def _seal(self, at: datetime) -> None:
        """End of a command: one version for everything it emitted."""
        assert self._pending, "a command must write at least one event"
        assert at >= self.last_time, "time must not go backwards"
        self.version += 1
        for event in self._pending:
            event.version = self.version
            event.at = at
        self.events.extend(self._pending)
        if any(e.actor is not None for e in self._pending):
            self.last_activity_at = at
        self.updated_at = at
        self.last_time = at
        self._pending = []

    def _require(self, action: str) -> None:
        if self.status not in TRANSITIONS[action]:
            raise InvalidTransition(f"{action} is not allowed from {self.status} ({self.key})")

    def _set(self, data: dict[str, Any], name: str, attr: str, new: Any) -> None:
        old = getattr(self, attr)
        if old != new:
            data[name] = change(old, new)
            setattr(self, attr, new)

    def content_hash(self) -> str:
        return subject_hash(self.team.id, self.type, self.title, self.description)

    def has_valid_approval(self) -> bool:
        return any(
            a.status == ApprovalStatus.APPROVED and a.subject_hash == self.content_hash()
            for a in self.approvals
        )

    def pending_approval(self) -> ApprovalRec | None:
        return next((a for a in self.approvals if a.status == ApprovalStatus.PENDING), None)

    def is_worker(self, user: uuid.UUID) -> bool:
        return user in self.team.worker_set

    # ----- ownership ------------------------------------------------------------------

    def claim(self, at: datetime, actor: uuid.UUID) -> None:
        self._require("claim")
        assert self.assignee is None and self.is_worker(actor)
        self.assignee = actor
        self.status = S.IN_PROGRESS
        self.watchers.add(actor)
        self._emit(EventKind.ASSIGNED, actor, {"assignee": change(None, actor)})
        self._emit(EventKind.STATUS_CHANGED, actor, {"status": change(S.NEW, S.IN_PROGRESS)})
        self._seal(at)

    def assign(self, at: datetime, lead: uuid.UUID, target: uuid.UUID) -> None:
        self._require("assign")
        assert lead in self.team.leads and self.is_worker(target) and target != self.assignee
        old_status = self.status
        data: dict[str, Any] = {"assignee": change(self.assignee, target)}
        self.assignee = target
        self.watchers.add(target)
        self.status = S.IN_PROGRESS if old_status == S.NEW else old_status
        self._emit(EventKind.ASSIGNED, lead, data)
        if old_status == S.NEW:
            self._emit(EventKind.STATUS_CHANGED, lead, {"status": change(S.NEW, S.IN_PROGRESS)})
        self._seal(at)

    def release(self, at: datetime, actor: uuid.UUID, *, by_lead: bool = False) -> None:
        self._require("unassign" if by_lead else "release")
        old = self.status
        data: dict[str, Any] = {"assignee": change(self.assignee, None)}
        self.assignee = None
        self.status = S.NEW
        reason = "Removed by a lead." if by_lead else None
        self._emit(EventKind.UNASSIGNED, actor, data, reason)
        self._emit(EventKind.STATUS_CHANGED, actor, {"status": change(old, S.NEW)})
        self._seal(at)

    def block(self, at: datetime, actor: uuid.UUID, reason: str) -> None:
        self._require("block")
        self.status = S.BLOCKED
        self._emit(EventKind.BLOCKED, actor, {"status": change(S.IN_PROGRESS, S.BLOCKED)}, reason)
        self._seal(at)

    def unblock(self, at: datetime, actor: uuid.UUID) -> None:
        self._require("unblock")
        self.status = S.IN_PROGRESS
        self._emit(EventKind.UNBLOCKED, actor, {"status": change(S.BLOCKED, S.IN_PROGRESS)})
        self._seal(at)

    # ----- approvals ------------------------------------------------------------------

    def request_approval(self, at: datetime, actor: uuid.UUID, note: str | None) -> None:
        self._require("request_approval")
        assert self.pending_approval() is None
        approval = ApprovalRec(
            id=uuid.UUID(int=self.rng.getrandbits(128), version=4),
            requested_by=actor,
            requested_at=at,
            request_note=note,
            subject_hash=self.content_hash(),
        )
        self.approvals.append(approval)
        self.status = S.AWAITING_APPROVAL
        data = {
            "approval_id": str(approval.id),
            "status": change(S.IN_PROGRESS, S.AWAITING_APPROVAL),
        }
        self._emit(EventKind.APPROVAL_REQUESTED, actor, data, note)
        self._seal(at)

    def decide(self, at: datetime, lead: uuid.UUID, *, approve: bool, note: str) -> None:
        self._require("decide")
        approval = self.pending_approval()
        assert approval is not None and lead in self.team.leads and lead != approval.requested_by
        approval.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED
        approval.decided_by = lead
        approval.decided_at = at
        approval.decision_note = note
        self.status = S.IN_PROGRESS
        kind = EventKind.APPROVAL_APPROVED if approve else EventKind.APPROVAL_REJECTED
        data = {
            "approval_id": str(approval.id),
            "status": change(S.AWAITING_APPROVAL, S.IN_PROGRESS),
        }
        self._emit(kind, lead, data, note, decision=True)
        self._seal(at)

    def cancel_approval(self, at: datetime, actor: uuid.UUID) -> None:
        self._require("cancel_approval")
        approval = self.pending_approval()
        assert approval is not None
        approval.status = ApprovalStatus.CANCELLED
        self.status = S.IN_PROGRESS
        data = {
            "approval_id": str(approval.id),
            "status": change(S.AWAITING_APPROVAL, S.IN_PROGRESS),
        }
        self._emit(EventKind.APPROVAL_CANCELLED, actor, data)
        self._seal(at)

    def _invalidate_approvals(self, actor: uuid.UUID, fields: list[str]) -> None:
        """A material field changed: pending and approved approvals no longer cover the content."""
        affected = [
            a
            for a in self.approvals
            if a.status == ApprovalStatus.PENDING
            or (a.status == ApprovalStatus.APPROVED and a.subject_hash != self.content_hash())
        ]
        if not affected:
            return
        data: dict[str, Any] = {"fields": fields, "approval_ids": [str(a.id) for a in affected]}
        reason = f"Content changed: {', '.join(fields)}"
        if any(a.status == ApprovalStatus.PENDING for a in affected):
            self.status = S.IN_PROGRESS
            data["status"] = change(S.AWAITING_APPROVAL, S.IN_PROGRESS)
        for a in affected:
            a.status = ApprovalStatus.INVALIDATED
            a.invalidated_reason = reason
        self._emit(EventKind.APPROVAL_INVALIDATED, actor, data, reason, decision=True)

    # ----- finishing and reopening ----------------------------------------------------

    def resolve(self, at: datetime, actor: uuid.UUID, note: str) -> None:
        self._require("resolve")
        if self.requires_approval and not self.has_valid_approval():
            raise InvalidTransition(f"{self.key} needs a valid approval before it can resolve")
        data: dict[str, Any] = {"status": change(S.IN_PROGRESS, S.RESOLVED)}
        self.status = S.RESOLVED
        self._set(data, "resolution", "resolution", Resolution.DONE)
        self.resolution_note = note
        self.resolved_at = at
        self._emit(EventKind.RESOLVED, actor, data, note, decision=True)
        self._seal(at)

    def close(
        self,
        at: datetime,
        actor: uuid.UUID,
        *,
        resolution: Resolution,
        reason: str,
        duplicate_of: uuid.UUID | None = None,
    ) -> None:
        self._require("close")
        old = self.status
        data: dict[str, Any] = {"status": change(old, S.CLOSED)}
        self.status = S.CLOSED
        if old != S.RESOLVED:
            self._set(data, "resolution", "resolution", resolution)
            self._set(data, "duplicate_of", "duplicate_of", duplicate_of)
        self.closed_at = at
        self._emit(EventKind.CLOSED, actor, data, reason, decision=True)
        self._seal(at)

    def withdraw(self, at: datetime, reason: str) -> None:
        """The requester gives up on an item nobody has started (SPEC 4.1)."""
        self._require("withdraw")
        data: dict[str, Any] = {"status": change(S.NEW, S.CLOSED)}
        self.status = S.CLOSED
        self._set(data, "resolution", "resolution", Resolution.WONT_DO)
        self.closed_at = at
        self._emit(EventKind.CLOSED, self.requester, data, reason, decision=True)
        self._seal(at)

    def reopen(self, at: datetime, actor: uuid.UUID, reason: str) -> None:
        """Back to in_progress with the previous owner if they can still work it, else to new."""
        self._require("reopen")
        old = self.status
        owner_ok = self.assignee is not None and self.is_worker(self.assignee)
        data: dict[str, Any] = {}
        self.status = S.IN_PROGRESS if owner_ok else S.NEW
        data["status"] = change(old, self.status)
        self._set(data, "assignee", "assignee", self.assignee if owner_ok else None)
        self._set(data, "resolution", "resolution", None)
        self._set(data, "duplicate_of", "duplicate_of", None)
        self.resolution_note = None
        self.resolved_at = None
        self.closed_at = None
        self.reopen_count += 1
        self._emit(EventKind.REOPENED, actor, data, reason, decision=True)
        self._seal(at)

    # ----- field edits ----------------------------------------------------------------

    def set_priority(
        self, at: datetime, actor: uuid.UUID, new: int, reason: str | None = None
    ) -> None:
        self._require("priority")
        lowered_from_urgent = new > self.priority and self.priority <= 1
        if lowered_from_urgent and reason is None:
            raise InvalidTransition("lowering from P0/P1 needs a reason")
        data: dict[str, Any] = {}
        self._set(data, "priority", "priority", new)
        if self.due_source == DueSource.AUTO:
            self._set(data, "due_at", "due_at", self.created_at + timedelta(hours=SLA_HOURS[new]))
        self._emit(EventKind.PRIORITY_CHANGED, actor, data, reason, decision=lowered_from_urgent)
        self._seal(at)

    def edit_text(self, at: datetime, actor: uuid.UUID, name: str, value: str) -> None:
        self._require("edit")
        data: dict[str, Any] = {}
        self._set(data, name, name, value)
        self._emit(EventKind.FIELD_CHANGED, actor, data)
        self._invalidate_approvals(actor, [name])
        self._seal(at)

    def set_due(self, at: datetime, lead: uuid.UUID, due: datetime) -> None:
        self._require("set_due")
        data: dict[str, Any] = {}
        self._set(data, "due_at", "due_at", due)
        self._set(data, "due_source", "due_source", DueSource.MANUAL)
        self._emit(EventKind.FIELD_CHANGED, lead, data)
        self._seal(at)

    def set_confidential(self, at: datetime, lead: uuid.UUID, value: bool, reason: str) -> None:
        self._require("confidential")
        data: dict[str, Any] = {}
        self._set(data, "confidential", "confidential", value)
        self._emit(EventKind.CONFIDENTIAL_CHANGED, lead, data, reason, decision=True)
        self._seal(at)

    def set_requires_approval(
        self, at: datetime, actor: uuid.UUID, value: bool, reason: str | None
    ) -> None:
        self._require("requires_approval")
        data: dict[str, Any] = {}
        self._set(data, "requires_approval", "requires_approval", value)
        turned_off = not value
        self._emit(EventKind.REQUIRES_APPROVAL_CHANGED, actor, data, reason, decision=turned_off)
        self._seal(at)

    def transfer(self, at: datetime, lead: uuid.UUID, target: TeamCtx, reason: str) -> None:
        """SPEC 4.3: team changes, key does not; approvals stop covering the content."""
        self._require("transfer")
        assert lead in self.team.leads and target is not self.team
        pending = self.pending_approval()
        if pending is not None:
            pending.status = ApprovalStatus.CANCELLED
            pending_data = {
                "approval_id": str(pending.id),
                "status": change(S.AWAITING_APPROVAL, S.IN_PROGRESS),
            }
            self.status = S.IN_PROGRESS
            self._emit(EventKind.APPROVAL_CANCELLED, lead, pending_data, "transferred")
        data: dict[str, Any] = {"team": change(self.team.id, target.id)}
        self.team = target
        keeps_owner = self.assignee is not None and self.is_worker(self.assignee)
        if self.assignee is not None and not keeps_owner:
            data["assignee"] = change(self.assignee, None)
            self.assignee = None
            if self.status != S.NEW:
                data["status"] = change(self.status, S.NEW)
                self.status = S.NEW
        self._emit(EventKind.TRANSFERRED, lead, data, reason, decision=True)
        self._invalidate_approvals(lead, ["team"])
        self._seal(at)

    # ----- collaboration and system events --------------------------------------------

    def comment(self, at: datetime, author: uuid.UUID, body: str) -> None:
        self.comments.append(CommentRec(author, body, at))
        self.watchers.add(author)
        self._emit(EventKind.COMMENTED, author, {"excerpt": body[:80]})
        self._seal(at)

    def breach(self, at: datetime) -> None:
        self.sla_breached_at = at
        data = {"due_at": ser(self.due_at), "sla_breached_at": ser(at)}
        self._emit(EventKind.SLA_BREACHED, None, data)
        self._seal(at)

    def suggest_duplicates(self, at: datetime, keys: list[str]) -> None:
        self._emit(EventKind.DUPLICATE_SUGGESTED, None, {"similar": keys})
        self._seal(at)


# ----- runner ----------------------------------------------------------------------------

TARGETS = (
    "new",
    "in_progress",
    "blocked",
    "awaiting",
    "resolved",
    "closed_done",
    "closed_other",
    "reopened",
)

# Typical gap between steps, in minutes, by priority.
_STEP_MINUTES = {0: 12, 1: 50, 2: 200, 3: 600}


def next_action(sim: ItemSim, target: str, rng: random.Random) -> str | None:
    """What the walk does next to move `sim` toward `target`; None when it is there."""
    s = sim.status
    if target == "new":
        return {
            S.IN_PROGRESS: "release",
            S.BLOCKED: "release",
            S.AWAITING_APPROVAL: "cancel_approval",
        }.get(s)
    if target == "in_progress":
        return {S.NEW: "claim", S.BLOCKED: "unblock", S.AWAITING_APPROVAL: "cancel_approval"}.get(s)
    if target == "blocked":
        return {S.NEW: "claim", S.IN_PROGRESS: "block", S.AWAITING_APPROVAL: "cancel_approval"}.get(
            s
        )
    if target == "awaiting":
        return {S.NEW: "claim", S.BLOCKED: "unblock", S.IN_PROGRESS: "request_approval"}.get(s)
    if target == "closed_other":
        if s == S.NEW:
            return rng.choice(["claim", "close", "withdraw", "close"])
        if s == S.AWAITING_APPROVAL:
            return "cancel_approval"
        return "close" if s in (S.IN_PROGRESS, S.BLOCKED) else None
    # resolved, closed_done, reopened: all need to get through resolve first
    if s == S.NEW:
        return "claim"
    if s == S.BLOCKED:
        return "unblock"
    if s == S.AWAITING_APPROVAL:
        return "decide"
    if s == S.IN_PROGRESS:
        if sim.reopen_count and target == "reopened":
            return None
        needs = sim.requires_approval and not sim.has_valid_approval()
        return "request_approval" if needs else "resolve"
    if s == S.RESOLVED:
        return {"resolved": None, "closed_done": "close", "reopened": "reopen"}[target]
    if s == S.CLOSED and target == "reopened" and not sim.reopen_count:
        return "reopen"
    return None


NOISE_ACTION = {
    "priority": "priority",
    "edit": "edit",
    "set_due": "set_due",
    "reassign": "assign",
    "handover": "release",
    "block_cycle": "block",
    "transfer": "transfer",
    "confidential": "confidential",
    "requires_approval": "requires_approval",
}


class Runner:
    def __init__(
        self,
        sim: ItemSim,
        rng: random.Random,
        now: datetime,
        *,
        noise: float,
        comment_rate: float,
        other_teams: list[TeamCtx],
        duplicate_candidates: list[uuid.UUID],
        transfer_weight: float,
        dwell_steps: int,
        dwell_comment_share: float,
    ) -> None:
        self.sim = sim
        self.rng = rng
        self.now = now
        self.noise = noise
        self.comment_rate = comment_rate
        self.other_teams = other_teams
        self.duplicate_candidates = duplicate_candidates
        self.transfer_weight = transfer_weight
        self.dwell_steps = dwell_steps
        self.dwell_comment_share = dwell_comment_share
        self.clock = sim.last_time

    def _tick(self) -> datetime | None:
        base = _STEP_MINUTES[self.sim.priority]
        minutes = base * self.rng.expovariate(1.0) * self.rng.uniform(0.3, 1.5)
        self.clock = self.clock + timedelta(seconds=max(20.0, min(minutes * 60, 5 * 86400)))
        return self.clock if self.clock <= self.now else None

    def sweep(self, until: datetime) -> None:
        """The SLA sweep: marks an overdue open item once, within a minute of its due time."""
        sim = self.sim
        overdue = (
            sim.status in OPEN
            and sim.sla_breached_at is None
            and sim.due_at is not None
            and sim.due_at <= until
        )
        if overdue and sim.due_at is not None:
            at = sim.due_at + timedelta(seconds=self.rng.randint(5, 55))
            at = max(at, sim.last_time + timedelta(seconds=1))
            if at <= until:
                sim.breach(at)

    def run(self, target: str, max_steps: int = 80) -> None:
        """Walk toward `target`. Once there, spend a few "dwell" steps (comments, small changes)
        so open items have a believable amount of history, then converge again."""
        sim = self.sim
        dwell = self.dwell_steps
        for _ in range(max_steps):
            action = next_action(sim, target, self.rng)
            if action is None:
                if dwell <= 0:
                    break
                dwell -= 1
                action = "dwell"
            at = self._tick()
            if at is None:
                break
            self.sweep(at)
            at = max(at, sim.last_time + timedelta(seconds=1))
            if at > self.now:
                break
            self.clock = at
            if action == "dwell":
                if self.rng.random() < self.dwell_comment_share:
                    self.add_comment(at)
                else:
                    self.maybe_noise(at)
                continue
            self.perform(action, at)
            if self.rng.random() < self.comment_rate:
                self.add_comment(at + timedelta(seconds=self.rng.randint(30, 900)))
            if self.rng.random() < self.noise:
                self.maybe_noise(at)
        self.sweep(self.now)

    def add_comment(self, at: datetime) -> None:
        sim = self.sim
        if at > self.now:
            return
        at = max(at, sim.last_time + timedelta(seconds=1))
        if at > self.now:
            return
        pool = [sim.requester, *sim.team.worker_pool]
        if sim.assignee is not None:
            pool.append(sim.assignee)
        if sim.team.viewers and self.rng.random() < 0.1:
            pool.append(self.rng.choice(sim.team.viewers))
        self.sim.comment(at, self.rng.choice(pool), text.make_comment(self.rng))

    # ----- performing actions ---------------------------------------------------------

    def perform(self, action: str, at: datetime) -> None:
        sim, rng = self.sim, self.rng
        assignee = sim.assignee
        lead = rng.choice(sim.team.leads)
        if action == "claim":
            sim.claim(at, rng.choice(sim.team.worker_pool))
        elif action == "release":
            assert assignee is not None
            sim.release(at, assignee)
        elif action == "block":
            assert assignee is not None
            sim.block(at, assignee, text.pick(rng, text.BLOCK_REASONS))
        elif action == "unblock":
            sim.unblock(at, assignee if assignee is not None else lead)
        elif action == "request_approval":
            sim.request_approval(
                at,
                assignee if assignee is not None else lead,
                text.pick(rng, text.APPROVAL_REQUEST_NOTES),
            )
        elif action == "decide":
            approval = sim.pending_approval()
            assert approval is not None
            approvers = [x for x in sim.team.leads if x != approval.requested_by]
            approve = rng.random() > 0.15
            pool = text.APPROVE_NOTES if approve else text.REJECT_NOTES
            sim.decide(at, rng.choice(approvers), approve=approve, note=text.pick(rng, pool))
        elif action == "cancel_approval":
            approval = sim.pending_approval()
            assert approval is not None
            sim.cancel_approval(at, approval.requested_by)
        elif action == "resolve":
            assert assignee is not None
            sim.resolve(at, assignee, text.pick(rng, text.RESOLUTION_NOTES))
        elif action == "withdraw":
            sim.withdraw(at, text.pick(rng, text.CLOSE_REASONS["wont_do"]))
        elif action == "reopen":
            candidates = [sim.requester, lead]
            if assignee is not None and sim.status == S.RESOLVED:
                candidates.append(assignee)
            actor = rng.choice(candidates) if sim.status == S.RESOLVED else lead
            sim.reopen(at, actor, text.pick(rng, text.REOPEN_REASONS))
        elif action == "close":
            self.close(at, lead)
        else:
            raise InvalidTransition(f"unknown action {action}")

    def close(self, at: datetime, lead: uuid.UUID) -> None:
        sim, rng = self.sim, self.rng
        if sim.status == S.RESOLVED:
            actor = lead if rng.random() < 0.7 else sim.requester
            sim.close(
                at,
                actor,
                resolution=Resolution.DONE,
                reason=text.pick(rng, text.CLOSE_REASONS["done"]),
            )
            return
        options = [Resolution.WONT_DO, Resolution.CANNOT_REPRODUCE]
        if self.duplicate_candidates:
            options += [Resolution.DUPLICATE] * 2
        resolution = rng.choice(options)
        duplicate_of = (
            rng.choice(self.duplicate_candidates) if resolution == Resolution.DUPLICATE else None
        )
        sim.close(
            at,
            lead,
            resolution=resolution,
            reason=text.pick(rng, text.CLOSE_REASONS[resolution.value]),
            duplicate_of=duplicate_of,
        )

    # ----- noise: things that happen along the way ------------------------------------

    def maybe_noise(self, at: datetime) -> None:
        sim, rng = self.sim, self.rng
        at = max(
            at + timedelta(seconds=rng.randint(60, 1800)), sim.last_time + timedelta(seconds=1)
        )
        if at > self.now or sim.status not in OPEN:
            return
        choices = ["priority", "edit", "set_due", "reassign"]
        weights: list[float] = [4, 2, 1, 2]
        if self.other_teams:
            choices.append("transfer")
            weights.append(self.transfer_weight)
        if sim.status in (S.IN_PROGRESS, S.BLOCKED):
            choices += ["handover", "block_cycle"]
            weights += [2, 2]
        choices += ["confidential", "requires_approval"]
        weights += [1, 1]
        self._noise(rng.choices(choices, weights)[0], at)

    def _noise(self, which: str, at: datetime) -> None:
        sim, rng = self.sim, self.rng
        # Noise may only do what the transition table allows from the current status.
        if sim.status not in TRANSITIONS[NOISE_ACTION[which]]:
            return
        lead = rng.choice(sim.team.leads)
        if which == "priority":
            new = rng.choice([p for p in range(4) if p != sim.priority])
            reason = text.pick(rng, text.LOWER_PRIORITY_REASONS) if new > sim.priority else None
            if new > sim.priority and sim.priority <= 1 and reason is None:
                return
            solo_requester = sim.status == S.NEW and sim.assignee is None and rng.random() < 0.3
            actor = sim.requester if solo_requester else rng.choice(sim.team.worker_pool)
            sim.set_priority(at, actor, new, reason)
        elif which == "edit":
            actor = sim.assignee if sim.assignee is not None else lead
            if rng.random() < 0.6:
                sim.edit_text(at, actor, "title", text.edited_title(rng, sim.title))
            else:
                sim.edit_text(
                    at, actor, "description", text.edited_description(rng, sim.description)
                )
        elif which == "set_due":
            sim.set_due(at, lead, at + timedelta(hours=rng.choice([6, 12, 48, 96, 240])))
        elif which == "reassign":
            if sim.status == S.AWAITING_APPROVAL:
                return
            others = [u for u in sim.team.worker_pool if u != sim.assignee]
            sim.assign(at, lead, rng.choice(others))
        elif which == "handover":
            if sim.status == S.AWAITING_APPROVAL or sim.assignee is None:
                return
            sim.release(at, sim.assignee)
        elif which == "block_cycle":
            if sim.status == S.IN_PROGRESS and sim.assignee is not None:
                sim.block(at, sim.assignee, text.pick(rng, text.BLOCK_REASONS))
        elif which == "transfer":
            target = rng.choice(self.other_teams)
            if target is not sim.team and len(target.leads) >= 2:
                sim.transfer(at, lead, target, text.pick(rng, text.TRANSFER_REASONS))
        elif which == "confidential":
            sim.set_confidential(
                at, lead, not sim.confidential, text.pick(rng, text.CONFIDENTIAL_REASONS)
            )
        elif which == "requires_approval":
            if sim.requires_approval:
                sim.set_requires_approval(
                    at, lead, False, text.pick(rng, text.APPROVAL_OFF_REASONS)
                )
            elif sim.status != S.NEW:
                actor = sim.assignee if sim.assignee is not None else lead
                sim.set_requires_approval(at, actor, True, None)
