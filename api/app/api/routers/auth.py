from datetime import timedelta

from fastapi import APIRouter, Request, Response

from app.api.constants import API_PREFIX
from app.api.cookies import clear_cookies, set_csrf_cookie, set_session_cookie
from app.api.deps import AppSettings, Conn, utcnow
from app.api.routers.me import build_me
from app.api.schemas.auth import LoginRequest, MeOut
from app.auth import sessions
from app.db.tx import CommandTx, run_command
from app.services import auth as auth_service

router = APIRouter(prefix=f"{API_PREFIX}/auth", tags=["auth"])


@router.post("/login")
async def login(
    body: LoginRequest, request: Request, response: Response, conn: Conn, settings: AppSettings
) -> MeOut:
    """Sign in. Sets the session cookie (HttpOnly) and a fresh CSRF cookie."""
    result = await auth_service.login(
        conn,
        email=body.email.strip(),
        password=body.password,
        user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        now=utcnow(),
        session_ttl=timedelta(hours=settings.session_ttl_hours),
    )
    set_session_cookie(response, request, result.session_token)
    set_csrf_cookie(response, request, result.csrf_token)
    return await build_me(conn, result.user_id, result.csrf_token)


@router.post("/logout", status_code=204)
async def logout(request: Request, conn: Conn) -> Response:
    """End the session on the server (the token stops working at once), then clear the cookies.
    Signing out when already signed out is not an error."""
    token = request.cookies.get(sessions.SESSION_COOKIE)
    if token:

        async def end_session(tx: CommandTx) -> None:
            await sessions.delete_session(tx.conn, token)

        await run_command(conn, end_session)
    response = Response(status_code=204)
    clear_cookies(response)
    return response
