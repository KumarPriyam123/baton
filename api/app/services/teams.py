"""Membership commands. Each one checks the policy, then applies the change in one command
transaction (CLAUDE.md I5): the membership row is read with a lock and changed before commit.

SPEC 5.2: leads manage members and viewers; only admins touch leads (adding, removing, promoting
to lead, or demoting from lead).

SPEC 4.3b: removing someone from a team, or demoting them to viewer, unassigns their open items in
the same transaction (workflow.plan_removal_unassign, written through record_event), so no item is
left owned by someone who can no longer work it. There is no deactivate-user endpoint yet; when
there is one it calls the same `commands.unassign_owned_items` for each of the person's teams.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.tx import CommandTx, run_command
from app.domain.enums import TeamRole
from app.domain.errors import Forbidden, WorkflowViolation
from app.domain.policy import Action, ActorContext, TeamFacts, can
from app.repo import teams as teams_repo
from app.services import commands


@dataclass(frozen=True)
class Outcome:
    role: TeamRole
    changed: bool
    unassigned: int = 0  # open items that went back to `new` because of this change


def _require(ctx: ActorContext, team: teams_repo.TeamRecord, *roles: TeamRole) -> None:
    """Managing a lead (as the current or the new role) is admin-only; anyone else is a lead's
    or an admin's call."""
    action = Action.MANAGE_LEADS if TeamRole.LEAD in roles else Action.MANAGE_MEMBERS
    decision = can(ctx, action, TeamFacts(team.id, team.name))
    if not decision.allowed:
        raise Forbidden(decision.reason)


def _already_in(team: teams_repo.TeamRecord, current: TeamRole) -> WorkflowViolation:
    return WorkflowViolation(
        f"They are already in {team.name} as {current.value}. Change the role instead."
    )


async def add_member(
    conn: AsyncConnection,
    ctx: ActorContext,
    team: teams_repo.TeamRecord,
    user_id: uuid.UUID,
    role: TeamRole,
) -> Outcome:
    _require(ctx, team, role)

    async def work(tx: CommandTx) -> Outcome:
        if await teams_repo.insert_membership(tx.conn, team.id, user_id, role):
            return Outcome(role, changed=True)
        # Already there, or added by someone else a moment ago: the role that won decides.
        current = await teams_repo.get_role(tx.conn, team.id, user_id)
        if current == role:
            return Outcome(role, changed=False)  # adding someone who is already there is a no-op
        if current is None:  # removed again in between; the caller can simply retry
            raise WorkflowViolation("That membership just changed. Try again.")
        raise _already_in(team, current)

    return await run_command(conn, work, actor_id=ctx.user_id)


async def change_role(
    conn: AsyncConnection,
    ctx: ActorContext,
    team: teams_repo.TeamRecord,
    user_id: uuid.UUID,
    new_role: TeamRole,
    request_id: str | None = None,
) -> Outcome | None:
    """None when the person is not in the team."""

    async def work(tx: CommandTx) -> Outcome | None:
        current = await teams_repo.get_role(tx.conn, team.id, user_id, for_update=True)
        if current is None:
            return None
        _require(ctx, team, current, new_role)
        if current == new_role:
            return Outcome(new_role, changed=False)
        unassigned = 0
        if new_role == TeamRole.VIEWER:  # a viewer cannot own items (SPEC 4.3b)
            unassigned = await commands.unassign_owned_items(
                tx, team_id=team.id, team_name=team.name, user_id=user_id
            )
        await teams_repo.update_role(tx.conn, team.id, user_id, new_role)
        return Outcome(new_role, changed=True, unassigned=unassigned)

    return await run_command(conn, work, actor_id=ctx.user_id, request_id=request_id)


async def remove_member(
    conn: AsyncConnection,
    ctx: ActorContext,
    team: teams_repo.TeamRecord,
    user_id: uuid.UUID,
    request_id: str | None = None,
) -> bool:
    """False when the person is not in the team."""

    async def work(tx: CommandTx) -> bool:
        current = await teams_repo.get_role(tx.conn, team.id, user_id, for_update=True)
        if current is None:
            return False
        _require(ctx, team, current)
        await commands.unassign_owned_items(
            tx, team_id=team.id, team_name=team.name, user_id=user_id
        )
        await teams_repo.delete_membership(tx.conn, team.id, user_id)
        return True

    return await run_command(conn, work, actor_id=ctx.user_id, request_id=request_id)
