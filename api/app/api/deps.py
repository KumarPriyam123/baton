"""FastAPI dependencies: a database connection and the signed-in actor."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from app.auth import sessions
from app.config import Settings
from app.domain.errors import Unauthenticated
from app.domain.policy import ActorContext
from app.repo.users import load_actor_context


def utcnow() -> datetime:
    return datetime.now(UTC)


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """One connection per request. Closing it rolls back anything that was not committed."""
    engine: AsyncEngine = request.app.state.engine
    async with engine.connect() as conn:
        yield conn


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


Conn = Annotated[AsyncConnection, Depends(get_conn)]
AppSettings = Annotated[Settings, Depends(get_settings_dep)]


@dataclass(frozen=True)
class Actor:
    """Who is making this request, with their roles read fresh from the database."""

    session: sessions.SessionUser
    ctx: ActorContext

    @property
    def user_id(self) -> uuid.UUID:
        return self.session.user_id


async def current_actor(request: Request, conn: Conn, settings: AppSettings) -> Actor:
    """401 unless the request carries a live session. Roles are never taken from the cookie
    (SPEC 5.3): they are loaded from `memberships` on every request."""
    token = request.cookies.get(sessions.SESSION_COOKIE)
    if not token:
        raise Unauthenticated("Sign in to continue.")
    ttl = timedelta(hours=settings.session_ttl_hours)
    session = await sessions.authenticate(conn, token, utcnow(), ttl)
    if session is None:
        raise Unauthenticated("Your session has expired. Sign in again.")
    request.state.user_id = str(session.user_id)  # shows up in the request log line
    ctx = await load_actor_context(conn, session.user_id)
    if ctx is None:  # the user vanished between the two queries
        raise Unauthenticated("Sign in to continue.")
    return Actor(session, ctx)


CurrentActor = Annotated[Actor, Depends(current_actor)]
