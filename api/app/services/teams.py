"""Membership commands. Each one checks the policy, then applies the change in one transaction.

SPEC 5.2: leads manage members and viewers; only admins touch leads (adding, removing, promoting
to lead, or demoting from lead).

SPEC 4.3b says removing or demoting someone unassigns their open items in the same transaction.
That needs record_event() and the workflow (phases 3 and 4). Until then these commands REFUSE to
remove or demote a person who still owns open items in the team, rather than leave items owned
by someone who can no longer work them. Phase 4 replaces the refusal with the automatic unassign.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection

from app.domain.enums import TeamRole
from app.domain.errors import Forbidden, WorkflowViolation
from app.domain.policy import Action, ActorContext, TeamFacts, can
from app.repo import teams as teams_repo


@dataclass(frozen=True)
class Outcome:
    role: TeamRole
    changed: bool


def _require(ctx: ActorContext, team: teams_repo.TeamRecord, *roles: TeamRole) -> None:
    """Managing a lead (as the current or the new role) is admin-only; anyone else is a lead's
    or an admin's call."""
    action = Action.MANAGE_LEADS if TeamRole.LEAD in roles else Action.MANAGE_MEMBERS
    decision = can(ctx, action, TeamFacts(team.id, team.name))
    if not decision.allowed:
        raise Forbidden(decision.reason)


async def _refuse_if_owns_open_items(
    conn: AsyncConnection, team: teams_repo.TeamRecord, user_id: uuid.UUID, what: str
) -> None:
    count = await teams_repo.count_open_items_owned(conn, team.id, user_id)
    if count:
        noun = "item" if count == 1 else "items"
        raise WorkflowViolation(
            f"They still own {count} open {noun} in {team.name}. Reassign or release "
            f"{'it' if count == 1 else 'them'} before you {what}."
        )


async def add_member(
    conn: AsyncConnection,
    ctx: ActorContext,
    team: teams_repo.TeamRecord,
    user_id: uuid.UUID,
    role: TeamRole,
) -> Outcome:
    _require(ctx, team, role)
    current = await teams_repo.get_role(conn, team.id, user_id)
    if current == role:
        return Outcome(role, changed=False)  # adding someone who is already there is a no-op
    if current is not None:
        raise WorkflowViolation(
            f"They are already in {team.name} as {current.value}. Change the role instead."
        )
    await teams_repo.insert_membership(conn, team.id, user_id, role)
    await conn.commit()
    return Outcome(role, changed=True)


async def change_role(
    conn: AsyncConnection,
    ctx: ActorContext,
    team: teams_repo.TeamRecord,
    user_id: uuid.UUID,
    new_role: TeamRole,
) -> Outcome | None:
    """None when the person is not in the team."""
    current = await teams_repo.get_role(conn, team.id, user_id)
    if current is None:
        return None
    _require(ctx, team, current, new_role)
    if current == new_role:
        return Outcome(new_role, changed=False)
    if new_role == TeamRole.VIEWER:  # a viewer cannot own items
        await _refuse_if_owns_open_items(conn, team, user_id, "make them a viewer")
    await teams_repo.update_role(conn, team.id, user_id, new_role)
    await conn.commit()
    return Outcome(new_role, changed=True)


async def remove_member(
    conn: AsyncConnection, ctx: ActorContext, team: teams_repo.TeamRecord, user_id: uuid.UUID
) -> bool:
    """False when the person is not in the team."""
    current = await teams_repo.get_role(conn, team.id, user_id)
    if current is None:
        return False
    _require(ctx, team, current)
    await _refuse_if_owns_open_items(conn, team, user_id, "remove them")
    await teams_repo.delete_membership(conn, team.id, user_id)
    await conn.commit()
    return True
