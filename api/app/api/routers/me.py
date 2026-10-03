import uuid

from fastapi import APIRouter, Request, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.constants import API_PREFIX
from app.api.cookies import set_csrf_cookie
from app.api.deps import Conn, CurrentActor
from app.api.schemas.auth import MembershipOut, MeOut, TeamRef, UserOut
from app.auth import sessions
from app.domain.errors import Unauthenticated
from app.repo import users as users_repo

router = APIRouter(prefix=API_PREFIX, tags=["me"])


async def build_me(conn: AsyncConnection, user_id: str | uuid.UUID, csrf_token: str) -> MeOut:
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    user = await users_repo.get_user(conn, uid)
    if user is None:
        raise Unauthenticated("Sign in to continue.")
    memberships = await users_repo.list_memberships(conn, uid)
    return MeOut(
        user=UserOut(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin),
        memberships=[
            MembershipOut(team=TeamRef(key=m.team_key, name=m.team_name), role=m.role)
            for m in memberships
        ],
        csrf_token=csrf_token,
    )


@router.get("/me")
async def me(request: Request, response: Response, actor: CurrentActor, conn: Conn) -> MeOut:
    """Who I am, my teams and roles, and the CSRF token to send with unsafe requests.

    If the CSRF cookie has gone missing (cleared, or a new browser profile with a copied
    session), a new one is issued here: this is where a client recovers from CSRF_FAILED.
    """
    csrf = request.cookies.get(sessions.CSRF_COOKIE)
    if not csrf:
        csrf = sessions.new_csrf_token()
        set_csrf_cookie(response, request, csrf)
    return await build_me(conn, actor.user_id, csrf)
