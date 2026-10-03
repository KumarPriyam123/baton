"""Server-side sessions in Postgres (SPEC 5.4).

The browser holds a random 32-byte token; the database holds only its sha256, so a leaked
`sessions` table cannot be replayed. A session is valid for 12 hours of inactivity: each use
pushes `expires_at` forward, written at most once a minute so reads do not become writes.
"""

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema

SESSION_COOKIE = "baton_session"
CSRF_COOKIE = "baton_csrf"
CSRF_HEADER = "X-CSRF-Token"
TOUCH_INTERVAL = timedelta(seconds=60)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class SessionUser:
    session_id: str
    user_id: uuid.UUID
    email: str
    name: str
    is_admin: bool


async def create_session(
    conn: AsyncConnection,
    token: str,
    user_id: uuid.UUID,
    user_agent: str | None,
    now: datetime,
    ttl: timedelta,
) -> None:
    await conn.execute(
        sa.insert(schema.sessions).values(
            id=token_hash(token),
            user_id=user_id,
            created_at=now,
            expires_at=now + ttl,
            last_seen_at=now,
            user_agent=user_agent,
        )
    )


async def authenticate(
    conn: AsyncConnection, token: str, now: datetime, ttl: timedelta
) -> SessionUser | None:
    """The user behind a session token, or None if it is unknown, expired, or the user was
    deactivated. A valid session is extended (at most once a minute)."""
    sessions, users = schema.sessions.c, schema.users.c
    session_id = token_hash(token)
    row = (
        await conn.execute(
            sa.select(
                sessions.user_id,
                sessions.expires_at,
                sessions.last_seen_at,
                users.email,
                users.name,
                users.is_admin,
                users.deactivated_at,
            )
            .select_from(schema.sessions.join(schema.users, users.id == sessions.user_id))
            .where(sessions.id == session_id)
        )
    ).first()
    if row is None or row.expires_at <= now or row.deactivated_at is not None:
        return None

    if now - row.last_seen_at >= TOUCH_INTERVAL:
        await conn.execute(
            sa.update(schema.sessions)
            .where(sessions.id == session_id)
            .values(last_seen_at=now, expires_at=now + ttl)
        )
        await conn.commit()
    return SessionUser(session_id, row.user_id, str(row.email), row.name, row.is_admin)


async def delete_session(conn: AsyncConnection, token: str) -> None:
    """Part of the caller's command transaction (logout)."""
    await conn.execute(sa.delete(schema.sessions).where(schema.sessions.c.id == token_hash(token)))
