import uuid
from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor
from app.api.schemas.teams import (
    AddMemberRequest,
    ChangeRoleRequest,
    MemberOut,
    MembersPage,
    TeamOut,
    TeamPerson,
)
from app.domain.enums import TeamRole
from app.domain.errors import NotFound, ValidationFailed
from app.repo import teams as teams_repo
from app.repo import users as users_repo
from app.services import teams as teams_service

router = APIRouter(prefix=f"{API_PREFIX}/teams", tags=["teams"])

NO_SUCH_TEAM = "This team doesn't exist."
NOT_A_MEMBER = "That person isn't a member of this team."


async def _team(conn: Conn, key: str) -> teams_repo.TeamRecord:
    team = await teams_repo.get_team_by_key(conn, key.upper())
    if team is None:
        raise NotFound(NO_SUCH_TEAM)
    return team


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


def _member_out(user_id: uuid.UUID, name: str, email: str, role: TeamRole) -> MemberOut:
    return MemberOut(user=TeamPerson(id=user_id, name=name, email=email), role=role)


@router.get("")
async def list_teams(actor: CurrentActor, conn: Conn) -> list[TeamOut]:
    """Every team, so a request can be routed to any of them (SPEC A2)."""
    teams = await teams_repo.list_teams(conn)
    return [
        TeamOut(
            id=t.id,
            key=t.key,
            name=t.name,
            description=t.description,
            my_role=actor.ctx.roles.get(t.id),
        )
        for t in teams
    ]


@router.get("/{key}/members")
async def list_members(
    key: str,
    actor: CurrentActor,
    conn: Conn,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> MembersPage:
    team = await _team(conn, key)
    after = decode_cursor(cursor) if cursor else None
    rows = await teams_repo.list_members(conn, team.id, limit=limit + 1, after=after)
    page, more = rows[:limit], len(rows) > limit
    return MembersPage(
        items=[_member_out(m.user_id, m.name, m.email, m.role) for m in page],
        next_cursor=encode_cursor(page[-1].name, page[-1].user_id) if more else None,
    )


@router.post("/{key}/members", status_code=201)
async def add_member(
    key: str, body: AddMemberRequest, actor: CurrentActor, conn: Conn, response: Response
) -> MemberOut:
    """Add someone to the team. Adding a person who already has this role is a harmless 200."""
    team = await _team(conn, key)
    if not await users_repo.user_exists(conn, body.user_id):
        raise ValidationFailed(
            "No such user.",
            errors=[{"field": "user_id", "message": "No such user.", "type": "value_error"}],
        )
    outcome = await teams_service.add_member(conn, actor.ctx, team, body.user_id, body.role)
    if not outcome.changed:
        response.status_code = 200
    return await _member(conn, team, body.user_id)


@router.patch("/{key}/members/{user_id}")
async def change_role(
    key: str,
    user_id: uuid.UUID,
    body: ChangeRoleRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
) -> MemberOut:
    """Change someone's role. Demoting them to viewer unassigns their open items (SPEC 4.3b)."""
    team = await _team(conn, key)
    outcome = await teams_service.change_role(
        conn, actor.ctx, team, user_id, body.role, request_id=_request_id(request)
    )
    if outcome is None:
        raise NotFound(NOT_A_MEMBER)
    return await _member(conn, team, user_id)


@router.delete("/{key}/members/{user_id}", status_code=204)
async def remove_member(
    key: str, user_id: uuid.UUID, request: Request, actor: CurrentActor, conn: Conn
) -> Response:
    """Remove someone from the team. Their open items there go back to `new` (SPEC 4.3b)."""
    team = await _team(conn, key)
    if not await teams_service.remove_member(
        conn, actor.ctx, team, user_id, request_id=_request_id(request)
    ):
        raise NotFound(NOT_A_MEMBER)
    return Response(status_code=204)


async def _member(conn: Conn, team: teams_repo.TeamRecord, user_id: uuid.UUID) -> MemberOut:
    user = await users_repo.get_user(conn, user_id)
    role = await teams_repo.get_role(conn, team.id, user_id)
    if user is None or role is None:  # removed by someone else a moment ago
        raise NotFound(NOT_A_MEMBER)
    return _member_out(user.id, user.name, user.email, role)
