"""Workflow commands (SPEC 4.1, 4.3, 4.4, 6.1, 6.4). One transaction per command (CLAUDE.md I5).

Every command has the same shape, in this order:

    lock the item row (404 if the actor may not see it)
    policy.py  - may THIS person do it?                         -> 403 with the reason
    approvals the item has (locked after the item)
    a pending approval, for request_approval only               -> 409   (decision 35)
    If-Match, for the commands that need it                     -> 412 with what changed
    workflow.py - is it valid for THIS state and payload?       -> 409 / 422 / 400
    write the change, then one record_event() per planned event

The pure modules say what to do; this one only reads, writes and orders. A claim is the exception
to "workflow.py judges": its state rule is the WHERE clause of the UPDATE (SPEC 6.1, decision 39).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.db.tx import CommandTx, record_event
from app.domain import workflow
from app.domain.enums import ApprovalStatus, Resolution, TeamRole
from app.domain.errors import Forbidden, NotFound, ValidationFailed, WorkflowViolation
from app.domain.hashing import subject_hash
from app.domain.policy import Action, ActorContext, can
from app.repo import approvals as approvals_repo
from app.repo import events as events_repo
from app.repo import items as items_repo
from app.repo import teams as teams_repo
from app.services import items as items_service
from app.services.items import ItemView

WORKERS = (TeamRole.MEMBER, TeamRole.LEAD)  # who can own an item (a viewer cannot)
UNASSIGN_BATCH = 100  # items unassigned per round when someone leaves a team (I8)


@dataclass(frozen=True)
class Result:
    view: ItemView
    changed: bool  # False: nothing was written (claiming twice, assigning the owner)


class ClaimLost(Exception):
    """Someone else owns the item. Carries it as the loser now sees it, and when it was taken."""

    def __init__(self, view: ItemView, claimed_at: datetime | None) -> None:
        super().__init__("already claimed")
        self.view = view
        self.claimed_at = claimed_at


# ----- the steps every command shares ---------------------------------------------------------


def _content_hash(item: items_repo.LockedItem) -> str:
    return subject_hash(item.team_id, item.type, item.title, item.description)


def _state(item: items_repo.LockedItem) -> workflow.TransitionState:
    return workflow.TransitionState(
        item.status, item.assignee_id, item.resolution, item.duplicate_of_id
    )


def _authorize(ctx: ActorContext, action: Action, item: items_repo.LockedItem) -> None:
    decision = can(ctx, action, items_service.locked_facts(item))
    if not decision.allowed:
        raise Forbidden(decision.reason)


async def _facts(
    tx: CommandTx, item: items_repo.LockedItem
) -> tuple[list[workflow.ApprovalFact], workflow.WorkflowFacts]:
    approvals = await approvals_repo.live_for_item(tx.conn, item.id)  # item first, then approvals
    facts = workflow.WorkflowFacts(
        status=item.status,
        assignee_id=item.assignee_id,
        requires_approval=item.requires_approval,
        has_pending_approval=any(a.status == ApprovalStatus.PENDING for a in approvals),
        has_valid_approval=workflow.has_valid_approval(approvals, _content_hash(item)),
    )
    return approvals, facts


def _allowed(verdict: workflow.Allowed | workflow.Violation) -> workflow.Allowed:
    if isinstance(verdict, workflow.Violation):
        raise verdict.error()
    return verdict


async def _write(
    tx: CommandTx,
    item: items_repo.LockedItem,
    updates: dict[str, Any],
    events: tuple[workflow.PlannedEvent, ...],
    *,
    prev_team_id: uuid.UUID | None = None,
) -> items_repo.ChangedItem:
    """One version bump for the whole command, then every event at that version (I1, I2)."""
    changed = await items_repo.update_item(tx.conn, item.id, item.version, updates, tx.now)
    if changed is None:  # impossible while the row is locked; never write on a guess
        raise RuntimeError(f"{item.key} moved while its row was locked")
    for event in events:
        await record_event(
            tx,
            changed,
            event.kind,
            data=event.data,
            reason=event.reason,
            is_decision=event.is_decision,
            prev_team_id=prev_team_id,
        )
    return changed


async def _result(tx: CommandTx, ctx: ActorContext, key: str, *, changed: bool) -> Result:
    return Result(await items_service.get_item(tx.conn, ctx, key, tx.now), changed)


def _can_work(role: TeamRole | None) -> bool:
    return role in WORKERS


# ----- ownership ------------------------------------------------------------------------------


async def claim(tx: CommandTx, ctx: ActorContext, key: str) -> Result:
    """SPEC 6.1. Lock, check that the actor may claim, then ONE conditional UPDATE decides."""
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, Action.CLAIM, item)
    # The role as it is now, locked: an owner who was just removed from the team is refused.
    if not _can_work(await teams_repo.share_lock_role(tx.conn, item.team_id, ctx.user_id)):
        raise Forbidden(f"Only members and leads of {item.team_name} can claim items.")

    plan = workflow.claim_plan(ctx.user_id, tx.now)
    claimed = await items_repo.claim_item(tx.conn, item.id, plan.updates, tx.now)
    if claimed is None:
        return await _claim_lost(tx, ctx, item)
    await items_repo.add_watcher(tx.conn, item.id, ctx.user_id)
    for event in plan.events:
        await record_event(tx, claimed, event.kind, data=event.data, reason=event.reason)
    return await _result(tx, ctx, item.key, changed=True)


async def _claim_lost(tx: CommandTx, ctx: ActorContext, item: items_repo.LockedItem) -> Result:
    """The UPDATE matched nothing. Mine already: fine, claiming twice is not an error. Someone
    else's: 409 with who and when. Neither (resolved, closed, blocked...): say why not."""
    owned = item.assignee_id is not None and item.status in workflow.OPEN_STATUSES
    if owned and item.assignee_id == ctx.user_id:
        return await _result(tx, ctx, item.key, changed=False)
    if owned:
        view = await items_service.get_item(tx.conn, ctx, item.key, tx.now)
        raise ClaimLost(view, await events_repo.last_assigned_at(tx.conn, item.id))
    _, facts = await _facts(tx, item)
    verdict = workflow.evaluate(Action.CLAIM, facts, workflow.Payload())
    if isinstance(verdict, workflow.Violation):
        raise verdict.error()
    raise RuntimeError("the claim guard and the workflow table disagree")  # a bug, never a 4xx


async def release(tx: CommandTx, ctx: ActorContext, key: str) -> Result:
    """Give up my own item. No If-Match: only the owner can do it, and the state is checked."""
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, Action.RELEASE, item)
    _, facts = await _facts(tx, item)
    allowed = _allowed(workflow.evaluate(Action.RELEASE, facts, workflow.Payload()))
    plan = workflow.plan_transition(
        Action.RELEASE, _state(item), allowed, actor_id=ctx.user_id, now=tx.now
    )
    await _write(tx, item, plan.updates, plan.events)
    return await _result(tx, ctx, item.key, changed=True)


async def assign(
    tx: CommandTx,
    ctx: ActorContext,
    key: str,
    expected_version: int,
    assignee_id: uuid.UUID | None,
    reason: str | None,
) -> Result:
    """Set the owner, or clear it (`assignee_id` null is the unassign of SPEC 4.1)."""
    action = Action.UNASSIGN if assignee_id is None else Action.ASSIGN
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, action, item)
    _, facts = await _facts(tx, item)
    await items_service.ensure_version(tx, ctx, item, expected_version)

    can_work = None
    if assignee_id is not None:
        can_work = _can_work(await teams_repo.share_lock_role(tx.conn, item.team_id, assignee_id))
    allowed = _allowed(
        workflow.evaluate(
            action, facts, workflow.Payload(reason=reason, new_assignee_can_work=can_work)
        )
    )
    if assignee_id is not None and assignee_id == item.assignee_id:
        return await _result(tx, ctx, item.key, changed=False)  # already theirs

    plan = workflow.plan_transition(
        action,
        _state(item),
        allowed,
        actor_id=ctx.user_id,
        now=tx.now,
        new_assignee_id=assignee_id,
    )
    await _write(tx, item, plan.updates, plan.events)
    if assignee_id is not None:
        await items_repo.add_watcher(tx.conn, item.id, assignee_id)
    return await _result(tx, ctx, item.key, changed=True)


# ----- block, unblock, resolve, reopen, close, withdraw ---------------------------------------


async def _duplicate_id(
    tx: CommandTx, ctx: ActorContext, item: items_repo.LockedItem, key: str | None
) -> uuid.UUID | None:
    """The item a duplicate points at. It must be one the actor can see (404 would leak, so it is
    a field error) and not the item itself."""
    if key is None:
        return None
    found = await items_repo.visible_item_id(tx.conn, ctx, key.upper())
    if found is None or found == item.id:
        message = "An item can't duplicate itself." if found else "No such item."
        raise ValidationFailed(
            message, errors=[{"field": "duplicate_of", "message": message, "type": "value_error"}]
        )
    return found


async def transition(
    tx: CommandTx,
    ctx: ActorContext,
    key: str,
    expected_version: int,
    action: Action,
    *,
    reason: str | None,
    resolution: Resolution | None,
    duplicate_of: str | None,
) -> Result:
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, action, item)
    _, facts = await _facts(tx, item)
    await items_service.ensure_version(tx, ctx, item, expected_version)

    duplicate_id = (
        await _duplicate_id(tx, ctx, item, duplicate_of)
        if action in (Action.RESOLVE, Action.CLOSE)
        else None
    )
    allowed = _allowed(
        workflow.evaluate(action, facts, workflow.Payload(reason, resolution, duplicate_id))
    )
    previous_owner_can_work = False
    if action == Action.REOPEN and item.assignee_id is not None:
        role = await teams_repo.share_lock_role(tx.conn, item.team_id, item.assignee_id)
        previous_owner_can_work = _can_work(role)

    plan = workflow.plan_transition(
        action,
        _state(item),
        allowed,
        actor_id=ctx.user_id,
        now=tx.now,
        previous_owner_can_work=previous_owner_can_work,
        note=reason,
    )
    await _write(tx, item, plan.updates, plan.events)
    return await _result(tx, ctx, item.key, changed=True)


# ----- transfer -------------------------------------------------------------------------------


async def transfer(
    tx: CommandTx,
    ctx: ActorContext,
    key: str,
    expected_version: int,
    team_key: str,
    reason: str | None,
) -> Result:
    """SPEC 4.3. The key stays; the actor, who led the old team, may no longer see the item, so
    the answer is rendered without the visibility filter (decision 38)."""
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, Action.TRANSFER, item)
    approvals, facts = await _facts(tx, item)
    await items_service.ensure_version(tx, ctx, item, expected_version)

    target = await teams_repo.get_team_by_key(tx.conn, team_key.upper())
    if target is None or target.id == item.team_id:
        message = "No such team." if target is None else f"It is already in {item.team_name}."
        raise ValidationFailed(
            message, errors=[{"field": "team_key", "message": message, "type": "value_error"}]
        )
    allowed = _allowed(workflow.evaluate(Action.TRANSFER, facts, workflow.Payload(reason=reason)))
    owner_follows = False
    if item.assignee_id is not None:
        role = await teams_repo.share_lock_role(tx.conn, target.id, item.assignee_id)
        owner_follows = _can_work(role)
    assert allowed.reason is not None  # TRANSFER requires one

    plan = workflow.plan_transfer(
        _state(item),
        from_team_id=item.team_id,
        to_team_id=target.id,
        assignee_can_work_there=owner_follows,
        approvals=approvals,
        reason=allowed.reason,
    )
    await approvals_repo.close_without_decision(
        tx.conn,
        [plan.cancelled] if plan.cancelled else [],
        status=ApprovalStatus.CANCELLED,
        reason="transferred",
    )
    await approvals_repo.close_without_decision(
        tx.conn,
        plan.invalidated,
        status=ApprovalStatus.INVALIDATED,
        reason=plan.invalidated_reason,
    )
    # Every event names the old team in its live-update notice, so its screens drop the item.
    await _write(tx, item, plan.updates, plan.events, prev_team_id=item.team_id)

    record = await items_repo.get_by_key_unchecked(tx.conn, ctx.user_id, item.key)
    assert record is not None
    return Result(items_service.build_view(record, ctx, tx.now), changed=True)


# ----- approvals ------------------------------------------------------------------------------


async def request_approval(
    tx: CommandTx, ctx: ActorContext, key: str, expected_version: int, note: str | None
) -> Result:
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, Action.REQUEST_APPROVAL, item)
    _, facts = await _facts(tx, item)
    # Before the version (decision 35): two requests made from the same view both carry the same
    # version, and the one that lost is told "already pending", not "stale".
    conflict = workflow.pending_conflict(Action.REQUEST_APPROVAL, facts)
    if conflict is not None:
        raise conflict.error()
    await items_service.ensure_version(tx, ctx, item, expected_version)
    allowed = _allowed(workflow.evaluate(Action.REQUEST_APPROVAL, facts, workflow.Payload()))

    approval_id = uuid.uuid4()
    await approvals_repo.insert_pending(
        tx.conn,
        approval_id=approval_id,
        item_id=item.id,
        requested_by=ctx.user_id,
        requested_at=tx.now,
        note=(note or "").strip() or None,
        content_hash=_content_hash(item),
    )
    plan = workflow.plan_transition(
        Action.REQUEST_APPROVAL,
        _state(item),
        allowed,
        actor_id=ctx.user_id,
        now=tx.now,
        approval_id=approval_id,
        note=(note or "").strip() or None,
    )
    await _write(tx, item, plan.updates, plan.events)
    return await _result(tx, ctx, item.key, changed=True)


async def _require_approval_of(
    tx: CommandTx, item: items_repo.LockedItem, approval_id: uuid.UUID
) -> None:
    """404 if the approval in the URL is not this item's at all (checked before anything that
    could leak or conflict)."""
    if not await approvals_repo.belongs_to_item(tx.conn, item.id, approval_id):
        raise NotFound("No such approval request on this item.")


def _require_waiting(approvals: list[workflow.ApprovalFact], approval_id: uuid.UUID) -> None:
    """409 if the approval in the URL is no longer the one waiting: it was cancelled, or an edit
    invalidated it, or it was already decided."""
    pending = next((a for a in approvals if a.id == approval_id), None)
    if pending is None or pending.status != ApprovalStatus.PENDING:
        raise WorkflowViolation("That approval request is no longer waiting for a decision.")


async def decide(
    tx: CommandTx,
    ctx: ActorContext,
    key: str,
    approval_id: uuid.UUID,
    expected_version: int,
    *,
    approve: bool,
    note: str | None,
) -> Result:
    """Approve or reject. `If-Match` is the version the approver reviewed: if anything moved since,
    they get 412 with the changes and must look again (SPEC 4.4)."""
    action = Action.APPROVE if approve else Action.REJECT
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, action, item)  # a lead, and not the person who asked
    await _require_approval_of(tx, item, approval_id)
    approvals, facts = await _facts(tx, item)
    await items_service.ensure_version(tx, ctx, item, expected_version)
    allowed = _allowed(workflow.evaluate(action, facts, workflow.Payload(reason=note)))
    _require_waiting(approvals, approval_id)

    await approvals_repo.decide(
        tx.conn,
        approval_id,
        status=ApprovalStatus.APPROVED if approve else ApprovalStatus.REJECTED,
        decided_by=ctx.user_id,
        decided_at=tx.now,
        note=(note or "").strip() or None,
    )
    plan = workflow.plan_transition(
        action,
        _state(item),
        allowed,
        actor_id=ctx.user_id,
        now=tx.now,
        approval_id=approval_id,
        note=note,
    )
    await _write(tx, item, plan.updates, plan.events)
    return await _result(tx, ctx, item.key, changed=True)


async def cancel_approval(
    tx: CommandTx, ctx: ActorContext, key: str, approval_id: uuid.UUID
) -> Result:
    """Withdraw my own request (or, as a lead, anyone's). No If-Match (SPEC 6.2)."""
    item = await items_service.lock_visible(tx, ctx, key)
    _authorize(ctx, Action.CANCEL_APPROVAL, item)
    await _require_approval_of(tx, item, approval_id)
    approvals, facts = await _facts(tx, item)
    allowed = _allowed(workflow.evaluate(Action.CANCEL_APPROVAL, facts, workflow.Payload()))
    _require_waiting(approvals, approval_id)

    await approvals_repo.close_without_decision(
        tx.conn, [approval_id], status=ApprovalStatus.CANCELLED, reason=None
    )
    plan = workflow.plan_transition(
        Action.CANCEL_APPROVAL,
        _state(item),
        allowed,
        actor_id=ctx.user_id,
        now=tx.now,
        approval_id=approval_id,
    )
    await _write(tx, item, plan.updates, plan.events)
    return await _result(tx, ctx, item.key, changed=True)


# ----- membership (SPEC 4.3b) -----------------------------------------------------------------


async def unassign_owned_items(
    tx: CommandTx, *, team_id: uuid.UUID, team_name: str, user_id: uuid.UUID
) -> int:
    """Everything this person owns in the team goes back to `new`, in the transaction that removes
    or demotes them, so no item is left owned by someone who can't work it. Items are locked in id
    order, a hundred at a time. Returns how many were unassigned."""
    reason = f"Removed from {team_name}"
    total = 0
    while True:
        keys = await items_repo.lock_keys_owned_by(tx.conn, team_id, user_id, UNASSIGN_BATCH)
        if not keys:
            return total
        for key in keys:
            item = await items_repo.lock_by_key(tx.conn, key)
            if item is None:  # cannot happen: we hold its lock
                continue
            approvals = await approvals_repo.live_for_item(tx.conn, item.id)
            pending = next((a for a in approvals if a.status == ApprovalStatus.PENDING), None)
            plan = workflow.plan_removal_unassign(_state(item), pending, reason)
            await approvals_repo.close_without_decision(
                tx.conn,
                [plan.cancelled] if plan.cancelled else [],
                status=ApprovalStatus.CANCELLED,
                reason=reason,
            )
            await _write(tx, item, plan.updates, plan.events)
            total += 1
