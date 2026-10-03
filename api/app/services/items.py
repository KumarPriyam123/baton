"""Item commands and reads (SPEC 4.2, 6.2, 6.4). One transaction per command (CLAUDE.md I5).

A command locks the item row first, judges it with policy.py (who) and workflow.py (is it valid
now), writes the change and its events through `record_event`, and returns the item as the actor
sees it. Nothing in here knows about HTTP.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.tx import CommandTx, record_event
from app.domain import workflow
from app.domain.enums import ApprovalStatus, EventKind, ItemStatus, ItemType, TeamRole
from app.domain.errors import Forbidden, NotFound, ValidationFailed
from app.domain.policy import Action, ActorContext, Decision, ItemFacts, can, can_view
from app.repo import approvals as approvals_repo
from app.repo import events as events_repo
from app.repo import items as items_repo
from app.repo import teams as teams_repo


@dataclass(frozen=True)
class ItemView:
    """An item as one actor sees it: the facts plus what they may do and should do next."""

    record: items_repo.ItemRecord
    allowed_actions: list[Action]
    next_step: workflow.NextStep


def _facts(record: items_repo.ItemRecord) -> ItemFacts:
    pending_by = (
        record.approval_requested_by_id
        if record.approval_status == ApprovalStatus.PENDING
        else None
    )
    return ItemFacts(
        team_id=record.team_id,
        requester_id=record.requester_id,
        assignee_id=record.assignee_id,
        confidential=record.confidential,
        status=record.status,
        team_name=record.team_name,
        pending_approval_requested_by=pending_by,
    )


def _workflow_facts(record: items_repo.ItemRecord) -> workflow.WorkflowFacts:
    # `has_valid_approval` is what `evaluate` needs to gate a resolve; `allowed_actions` never
    # reads it (it offers Resolve and lets the server answer APPROVAL_REQUIRED, SPEC 12).
    return workflow.WorkflowFacts(
        status=record.status,
        assignee_id=record.assignee_id,
        requires_approval=record.requires_approval,
        has_pending_approval=record.approval_status == ApprovalStatus.PENDING,
        has_valid_approval=False,
    )


def build_view(record: items_repo.ItemRecord, ctx: ActorContext, now: datetime) -> ItemView:
    facts = _facts(record)
    allowed = workflow.allowed_actions(ctx, facts, _workflow_facts(record))
    role = ctx.roles.get(record.team_id)
    step = workflow.next_step(
        status=record.status,
        priority=record.priority,
        assignee_name=record.assignee_name,
        team_name=record.team_name,
        can_approve=can(ctx, Action.APPROVE, facts).allowed,
        sla_breached=record.sla_breached_at is not None,
        due_at=record.due_at,
        last_activity_at=record.last_activity_at,
        now=now,
        viewer_is_lead=role == TeamRole.LEAD,
        viewer_is_requester=record.requester_id == ctx.user_id,
        blocked_reason=record.blocked_reason,
    )
    return ItemView(record, allowed, step)


# ----- reads --------------------------------------------------------------------------------


async def get_item(conn: AsyncConnection, ctx: ActorContext, key: str, now: datetime) -> ItemView:
    record = await items_repo.get_visible_by_key(conn, ctx, key)
    if record is None:
        raise NotFound(Decision.not_found().reason)
    return build_view(record, ctx, now)


async def list_events(
    conn: AsyncConnection,
    ctx: ActorContext,
    key: str,
    *,
    after_id: int,
    descending: bool,
    limit: int,
) -> tuple[list[events_repo.EventRecord], int | None]:
    item_id = await items_repo.visible_item_id(conn, ctx, key)
    if item_id is None:
        raise NotFound(Decision.not_found().reason)
    return await events_repo.list_events(
        conn, item_id, after_id=after_id, descending=descending, limit=limit
    )


# ----- create -------------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateItem:
    team_key: str
    type: ItemType
    title: str
    description: str
    priority: int
    requires_approval: bool  # asking for approval at creation (ops tasks only)


async def create_item(tx: CommandTx, ctx: ActorContext, command: CreateItem) -> ItemView:
    if not can(ctx, Action.RAISE_ITEM).allowed:  # anyone signed in may (SPEC A2)
        raise Forbidden("You can't raise requests.")
    team = await teams_repo.get_team_by_key(tx.conn, command.team_key.upper())
    if team is None:
        raise ValidationFailed(
            "No such team.",
            errors=[{"field": "team_key", "message": "No such team.", "type": "value_error"}],
        )
    defaults = workflow.type_defaults(command.type)
    requires_approval = workflow.initial_requires_approval(command.type, command.requires_approval)

    key, number = await items_repo.allocate_number(tx.conn, team.id)
    tx.restamp()  # the team row lock may have been a wait
    item = await items_repo.insert_item(
        tx.conn,
        {
            "key": key,
            "team_id": team.id,
            "number": number,
            "origin_team_id": team.id,
            "type": command.type.value,
            "title": command.title,
            "description": command.description,
            "priority": command.priority,
            "status": ItemStatus.NEW.value,
            "requester_id": ctx.user_id,
            "confidential": defaults.confidential,
            "requires_approval": requires_approval,
            "due_at": workflow.due_for_priority(tx.now, command.priority),
            "due_source": "auto",
            "created_at": tx.now,
            "updated_at": tx.now,
            "last_activity_at": tx.now,
            "version": 1,
        },
    )
    await items_repo.add_watcher(tx.conn, item.id, ctx.user_id)
    await record_event(
        tx,
        item,
        EventKind.CREATED,
        data={
            "team": team.key,
            "type": command.type.value,
            "title": command.title,
            "priority": command.priority,
            "confidential": defaults.confidential,
            "requires_approval": requires_approval,
        },
    )
    return await get_item(tx.conn, ctx, key, tx.now)


# ----- edit ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class EditItem:
    """The fields the client sent (only those), plus how to read them."""

    fields: dict[str, Any]
    reason: str | None = None
    apply_type_defaults: bool = False


@dataclass(frozen=True)
class EditResult:
    view: ItemView
    changed: bool


class StaleVersion(Exception):
    """`If-Match` named an older version. Carries what the client needs to catch up."""

    def __init__(self, current: ItemView, changes_since: list[events_repo.EventRecord]) -> None:
        super().__init__("version moved")
        self.current = current
        self.changes_since = changes_since


def locked_facts(item: items_repo.LockedItem) -> ItemFacts:
    return ItemFacts(
        team_id=item.team_id,
        requester_id=item.requester_id,
        assignee_id=item.assignee_id,
        confidential=item.confidential,
        status=item.status,
        team_name=item.team_name,
        pending_approval_requested_by=item.pending_approval_requested_by,
    )


async def lock_visible(tx: CommandTx, ctx: ActorContext, key: str) -> items_repo.LockedItem:
    """First step of every command on an existing item (SPEC 6.4): lock its row, and treat an item
    the actor may not see exactly like a missing one (404)."""
    # Look before locking: someone who cannot see the item must not be able to tell it exists by
    # waiting on another command's row lock (a 503 where a missing key is an instant 404).
    if await items_repo.visible_item_id(tx.conn, ctx, key) is None:
        raise NotFound(Decision.not_found().reason)
    item = await items_repo.lock_by_key(tx.conn, key)
    tx.restamp()  # the lock may have been a wait; stamp the change at the time it happens
    if item is None or not can_view(ctx, locked_facts(item)):
        raise NotFound(Decision.not_found().reason)
    return item


async def ensure_version(
    tx: CommandTx, ctx: ActorContext, item: items_repo.LockedItem, expected_version: int
) -> None:
    """412 with the item as it is now and what changed since the actor looked (SPEC 6.2)."""
    if item.version != expected_version:
        current = await get_item(tx.conn, ctx, item.key, tx.now)
        since = await events_repo.changes_since(tx.conn, item.id, expected_version)
        raise StaleVersion(current, since)


async def edit_item(
    tx: CommandTx, ctx: ActorContext, key: str, expected_version: int, edit: EditItem
) -> EditResult:
    """PATCH (SPEC 4.2, 6.2): lock, who may, which version, what is valid, write, record."""
    item = await lock_visible(tx, ctx, key)
    facts = locked_facts(item)

    for action in workflow.required_actions(edit.fields):
        decision = can(ctx, action, facts)
        if not decision.allowed:
            raise Forbidden(decision.reason)

    await ensure_version(tx, ctx, item, expected_version)

    approvals = await approvals_repo.live_for_item(tx.conn, item.id)  # item first, then approvals
    plan = workflow.plan_edit(
        workflow.ItemState(
            team_id=item.team_id,
            type=item.type,
            title=item.title,
            description=item.description,
            priority=item.priority,
            status=item.status,
            due_at=item.due_at,
            due_source=item.due_source,
            created_at=item.created_at,
            confidential=item.confidential,
            requires_approval=item.requires_approval,
        ),
        edit.fields,
        apply_type_defaults=edit.apply_type_defaults,
        reason=edit.reason,
        approvals=approvals,
    )
    if not plan.changed:  # nothing to write: no version, no event
        return EditResult(await get_item(tx.conn, ctx, key, tx.now), changed=False)

    changed = await items_repo.update_item(
        tx.conn, item.id, expected_version, _columns(plan.updates), tx.now
    )
    if changed is None:  # unreachable while the row is locked; never write on a guess
        raise StaleVersion(
            await get_item(tx.conn, ctx, key, tx.now),
            await events_repo.changes_since(tx.conn, item.id, expected_version),
        )
    await approvals_repo.close_without_decision(
        tx.conn,
        plan.invalidated,
        status=ApprovalStatus.INVALIDATED,
        reason=plan.invalidated_reason,
    )
    for event in plan.events:
        await record_event(
            tx,
            changed,
            event.kind,
            data=event.data,
            reason=event.reason,
            is_decision=event.is_decision,
        )
    return EditResult(await get_item(tx.conn, ctx, key, tx.now), changed=True)


def _columns(updates: dict[str, Any]) -> dict[str, Any]:
    """Enum members become their stored strings."""
    return {k: getattr(v, "value", v) for k, v in updates.items()}
