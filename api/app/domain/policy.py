"""Authorization (SPEC 5): who may see and do what. Pure: no I/O.

Two forms of the visibility rule live here and must agree (T-VIS parity test):
- can_view(ctx, item)      for one item whose facts are in memory;
- visibility_clause(ctx)   a SQL predicate for every query that returns or counts items.

`can()` covers every row of the SPEC 5.2 matrix. It judges relationships and roles (and the few
cells that depend on status, such as "requester, while new and unassigned"). Whether an action
is valid from the item's current state is the workflow's job (phase 4), not this module's.

Item not visible  -> Decision(code="NOT_FOUND")  (404: its existence must not leak)
Visible, forbidden -> Decision(code="FORBIDDEN", reason=...)  (403, the reason is shown)
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.sql.elements import ColumnElement

from app.db import schema
from app.domain.enums import ItemStatus, TeamRole


class Action(StrEnum):
    # item actions (SPEC 5.2 rows, plus the requires_approval rule of SPEC 4.2)
    VIEW = "view"
    COMMENT = "comment"
    WATCH = "watch"
    EDIT_TEXT = "edit_text"  # title and description
    CHANGE_PRIORITY = "change_priority"
    EDIT_TYPE = "edit_type"
    EDIT_DUE_AT = "edit_due_at"
    EDIT_CONFIDENTIAL = "edit_confidential"
    REQUIRES_APPROVAL_ON = "requires_approval_on"
    REQUIRES_APPROVAL_OFF = "requires_approval_off"
    CLAIM = "claim"
    RELEASE = "release"
    ASSIGN = "assign"
    UNASSIGN = "unassign"
    BLOCK = "block"
    UNBLOCK = "unblock"
    RESOLVE = "resolve"
    REQUEST_APPROVAL = "request_approval"
    APPROVE = "approve"
    REJECT = "reject"
    CANCEL_APPROVAL = "cancel_approval"
    REOPEN = "reopen"  # resolved: assignee, lead, requester; closed: lead only
    CLOSE = "close"
    WITHDRAW = "withdraw"
    TRANSFER = "transfer"
    # team and organisation actions
    RAISE_ITEM = "raise_item"
    MANAGE_MEMBERS = "manage_members"  # members and viewers
    MANAGE_LEADS = "manage_leads"
    CREATE_TEAM = "create_team"
    VIEW_JOBS = "view_jobs"


ITEM_ACTIONS = frozenset(
    a
    for a in Action
    if a
    not in {
        Action.RAISE_ITEM,
        Action.MANAGE_MEMBERS,
        Action.MANAGE_LEADS,
        Action.CREATE_TEAM,
        Action.VIEW_JOBS,
    }
)
TEAM_ACTIONS = frozenset({Action.MANAGE_MEMBERS, Action.MANAGE_LEADS})

_VERB = {
    Action.COMMENT: "comment",
    Action.WATCH: "watch",
    Action.EDIT_TEXT: "edit the title or description",
    Action.CHANGE_PRIORITY: "change the priority",
    Action.EDIT_TYPE: "change the type",
    Action.EDIT_DUE_AT: "change the due date",
    Action.EDIT_CONFIDENTIAL: "change confidentiality",
    Action.REQUIRES_APPROVAL_ON: "require approval",
    Action.REQUIRES_APPROVAL_OFF: "turn off the approval requirement",
    Action.CLAIM: "claim items",
    Action.RELEASE: "release it",
    Action.ASSIGN: "assign it",
    Action.UNASSIGN: "unassign it",
    Action.BLOCK: "block it",
    Action.UNBLOCK: "unblock it",
    Action.RESOLVE: "resolve it",
    Action.REQUEST_APPROVAL: "request approval",
    Action.APPROVE: "approve",
    Action.REJECT: "reject",
    Action.CANCEL_APPROVAL: "cancel the approval request",
    Action.REOPEN: "reopen it",
    Action.CLOSE: "close it",
    Action.WITHDRAW: "withdraw it",
    Action.TRANSFER: "transfer it",
}


@dataclass(frozen=True)
class ActorContext:
    """Who is asking. Loaded fresh from the database on every request (SPEC 5.3)."""

    user_id: uuid.UUID
    is_admin: bool
    roles: Mapping[uuid.UUID, TeamRole]  # team id -> the user's role in that team

    def lead_team_ids(self) -> list[uuid.UUID]:
        return [team for team, role in self.roles.items() if role == TeamRole.LEAD]


@dataclass(frozen=True)
class ItemFacts:
    """The facts about one item that authorization depends on."""

    team_id: uuid.UUID
    requester_id: uuid.UUID
    assignee_id: uuid.UUID | None
    confidential: bool
    status: ItemStatus
    team_name: str = "the team"
    pending_approval_requested_by: uuid.UUID | None = None


@dataclass(frozen=True)
class TeamFacts:
    team_id: uuid.UUID
    team_name: str = "the team"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str | None = None  # "NOT_FOUND" or "FORBIDDEN" when not allowed
    reason: str | None = None

    @staticmethod
    def yes() -> "Decision":
        return Decision(True)

    @staticmethod
    def forbidden(reason: str) -> "Decision":
        return Decision(False, "FORBIDDEN", reason)

    @staticmethod
    def not_found() -> "Decision":
        return Decision(False, "NOT_FOUND", "This item doesn't exist or you no longer have access.")


# ----- visibility (SPEC 5.1) ---------------------------------------------------------------


def can_view(ctx: ActorContext, item: ItemFacts) -> bool:
    """Admin, the requester, or someone in the item's team who is allowed to see it.

    Inside the team a confidential item is for leads and its assignee only (SPEC A12).
    """
    if ctx.is_admin or item.requester_id == ctx.user_id:
        return True
    role = ctx.roles.get(item.team_id)
    if role is None:
        return False
    return not item.confidential or role == TeamRole.LEAD or item.assignee_id == ctx.user_id


def visibility_clause(ctx: ActorContext) -> ColumnElement[bool]:
    """The same rule as can_view, as SQL over `work_items`. Every query that returns or counts
    items must filter with this, in the WHERE clause, before LIMIT (never in Python after)."""
    if ctx.is_admin:
        return sa.true()
    wi = schema.work_items.c
    conditions: list[ColumnElement[bool]] = [wi.requester_id == ctx.user_id]
    team_ids = list(ctx.roles)
    if team_ids:
        inside_the_team: list[ColumnElement[bool]] = [
            wi.confidential.is_(False),
            wi.assignee_id == ctx.user_id,
        ]
        lead_ids = ctx.lead_team_ids()
        if lead_ids:
            inside_the_team.append(wi.team_id.in_(lead_ids))
        conditions.append(sa.and_(wi.team_id.in_(team_ids), sa.or_(*inside_the_team)))
    return sa.or_(*conditions)


# ----- permissions (SPEC 5.2) --------------------------------------------------------------


def can(ctx: ActorContext, action: Action, facts: ItemFacts | TeamFacts | None = None) -> Decision:
    if action in ITEM_ACTIONS:
        if not isinstance(facts, ItemFacts):
            raise TypeError(f"{action} needs ItemFacts")
        return _can_item(ctx, action, facts)
    if action in TEAM_ACTIONS:
        if not isinstance(facts, TeamFacts):
            raise TypeError(f"{action} needs TeamFacts")
        return _can_team(ctx, action, facts)
    if action == Action.RAISE_ITEM:
        return Decision.yes()  # anyone signed in can raise a request to any team (SPEC A2)
    return _can_organisation(ctx, action)


def _can_organisation(ctx: ActorContext, action: Action) -> Decision:
    if ctx.is_admin:
        return Decision.yes()
    what = "create teams" if action == Action.CREATE_TEAM else "view jobs"
    return Decision.forbidden(f"Only admins can {what}.")


def _can_team(ctx: ActorContext, action: Action, team: TeamFacts) -> Decision:
    if ctx.is_admin:
        return Decision.yes()
    if action == Action.MANAGE_LEADS:
        return Decision.forbidden("Only admins can manage leads.")
    if ctx.roles.get(team.team_id) == TeamRole.LEAD:
        return Decision.yes()
    return Decision.forbidden(f"Only leads of {team.team_name} (or admins) can manage its members.")


def _can_item(ctx: ActorContext, action: Action, item: ItemFacts) -> Decision:
    if not can_view(ctx, item):
        return Decision.not_found()
    if action == Action.VIEW:
        return Decision.yes()

    role = ctx.roles.get(item.team_id)
    is_lead = role == TeamRole.LEAD
    is_member = role in (TeamRole.MEMBER, TeamRole.LEAD)
    is_requester = item.requester_id == ctx.user_id
    is_assignee = item.assignee_id == ctx.user_id
    name = item.team_name
    status = item.status
    works_it = is_lead or (is_member and is_assignee)  # the "A / lead" cells of the matrix

    allowed: bool
    why = f"Only the assignee or a lead of {name} can {_VERB.get(action, action.value)}."
    match action:
        case Action.COMMENT | Action.WATCH:
            allowed = ctx.is_admin or is_requester or role is not None
            why = "You can't take part in this item."
        case Action.EDIT_TEXT:
            allowed = works_it or (is_requester and status not in _FINISHED)
            why = (
                "Only the requester (until it is resolved), the assignee or a lead of "
                f"{name} can edit the title or description."
            )
        case Action.CHANGE_PRIORITY:
            allowed = is_member or (
                is_requester and status == ItemStatus.NEW and item.assignee_id is None
            )
            why = (
                f"Members and leads of {name} can change the priority; the requester only "
                "while the item is new and unassigned."
            )
        case Action.CLAIM:
            allowed = is_member
            why = f"Only members and leads of {name} can claim items."
        case Action.RELEASE:
            allowed = is_member and is_assignee
            why = "Only the assignee can release an item."
        case (
            Action.EDIT_TYPE
            | Action.EDIT_DUE_AT
            | Action.EDIT_CONFIDENTIAL
            | Action.REQUIRES_APPROVAL_OFF
            | Action.ASSIGN
            | Action.UNASSIGN
            | Action.TRANSFER
        ):
            allowed = is_lead
            why = f"Only leads of {name} can {_VERB[action]}."
        case (
            Action.BLOCK
            | Action.UNBLOCK
            | Action.RESOLVE
            | Action.REQUEST_APPROVAL
            | Action.REQUIRES_APPROVAL_ON
        ):
            allowed = works_it
        case Action.APPROVE | Action.REJECT:
            if not is_lead:
                allowed = False
                why = f"Only leads of {name} can {_VERB[action]}."
            elif item.pending_approval_requested_by == ctx.user_id:
                allowed = False
                why = "You requested this approval, so someone else must decide it."
            else:
                allowed = True
        case Action.CANCEL_APPROVAL:
            asked = item.pending_approval_requested_by == ctx.user_id
            allowed = is_lead or (is_member and asked)
            why = f"Only the person who asked, or a lead of {name}, can cancel the request."
        case Action.REOPEN:
            if status == ItemStatus.CLOSED:
                allowed = is_lead
                why = f"Only leads of {name} can reopen a closed item."
            else:
                allowed = works_it or is_requester
                why = f"Only the requester, the assignee or a lead of {name} can reopen it."
        case Action.CLOSE:
            allowed = is_lead or (is_requester and status == ItemStatus.RESOLVED)
            why = (
                f"Only leads of {name} can close an item; the requester can close it once "
                "it is resolved."
            )
        case Action.WITHDRAW:
            allowed = is_requester and status == ItemStatus.NEW
            why = "Only the requester can withdraw an item, and only while it is new."
        case _:
            raise ValueError(f"unhandled action {action}")

    return Decision.yes() if allowed else Decision.forbidden(why)


_FINISHED = frozenset({ItemStatus.RESOLVED, ItemStatus.CLOSED})
