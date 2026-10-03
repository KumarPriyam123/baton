"""User and membership queries."""

import uuid
from dataclasses import dataclass
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import TeamRole
from app.domain.policy import ActorContext


async def load_actor_context(conn: AsyncConnection, user_id: uuid.UUID) -> ActorContext | None:
    """The user's admin flag and role per team, read fresh from the database (SPEC 5.3).

    Nothing about roles is cached anywhere: removing a membership takes effect on the next call.
    """
    users, memberships = schema.users, schema.memberships
    admin = (
        await conn.execute(sa.select(users.c.is_admin).where(users.c.id == user_id))
    ).scalar_one_or_none()
    if admin is None:
        return None
    rows = await conn.execute(
        sa.select(memberships.c.team_id, memberships.c.role).where(memberships.c.user_id == user_id)
    )
    roles = {team_id: TeamRole(role) for team_id, role in rows}
    return ActorContext(user_id=user_id, is_admin=bool(admin), roles=roles)


@dataclass(frozen=True)
class UserRecord:
    id: uuid.UUID
    email: str
    name: str
    password_hash: str
    is_admin: bool
    failed_logins: int
    locked_until: datetime | None
    deactivated_at: datetime | None


async def find_by_email(conn: AsyncConnection, email: str) -> UserRecord | None:
    u = schema.users.c
    row = (
        await conn.execute(
            sa.select(
                u.id,
                u.email,
                u.name,
                u.password_hash,
                u.is_admin,
                u.failed_logins,
                u.locked_until,
                u.deactivated_at,
            ).where(u.email == email)  # citext: case-insensitive
        )
    ).first()
    if row is None:
        return None
    return UserRecord(
        id=row.id,
        email=str(row.email),
        name=row.name,
        password_hash=row.password_hash,
        is_admin=row.is_admin,
        failed_logins=row.failed_logins,
        locked_until=row.locked_until,
        deactivated_at=row.deactivated_at,
    )


# One statement, with the row locked, so two simultaneous wrong passwords cannot both read the
# same counter. A lock that has already expired restarts the count at 1 instead of re-locking.
_REGISTER_FAILURE = sa.text(
    """
    UPDATE users SET
        failed_logins = n.failures,
        locked_until = CASE WHEN n.failures >= CAST(:max_failures AS integer)
                            THEN CAST(:lock_until AS timestamptz) ELSE NULL END
    FROM (
        SELECT id,
               CASE WHEN locked_until IS NOT NULL AND locked_until <= CAST(:now AS timestamptz)
                    THEN 1 ELSE failed_logins + 1 END AS failures
          FROM users WHERE id = CAST(:user_id AS uuid) FOR UPDATE
    ) n
    WHERE users.id = n.id
    RETURNING users.failed_logins, users.locked_until
    """
)


async def register_failed_login(
    conn: AsyncConnection,
    user_id: uuid.UUID,
    *,
    now: datetime,
    max_failures: int,
    lock_until: datetime,
) -> tuple[int, datetime | None]:
    row = (
        await conn.execute(
            _REGISTER_FAILURE,
            {
                "user_id": user_id,
                "now": now,
                "max_failures": max_failures,
                "lock_until": lock_until,
            },
        )
    ).one()
    return int(row.failed_logins), row.locked_until


async def clear_failed_logins(conn: AsyncConnection, user_id: uuid.UUID) -> None:
    await conn.execute(
        sa.update(schema.users)
        .where(schema.users.c.id == user_id)
        .values(failed_logins=0, locked_until=None)
    )


@dataclass(frozen=True)
class MembershipRecord:
    team_key: str
    team_name: str
    role: TeamRole


async def list_memberships(conn: AsyncConnection, user_id: uuid.UUID) -> list[MembershipRecord]:
    t, m = schema.teams.c, schema.memberships.c
    rows = await conn.execute(
        sa.select(t.key, t.name, m.role)
        .select_from(schema.memberships.join(schema.teams, t.id == m.team_id))
        .where(m.user_id == user_id)
        .order_by(t.name)
    )
    return [MembershipRecord(r.key, r.name, TeamRole(r.role)) for r in rows]


@dataclass(frozen=True)
class UserSummary:
    id: uuid.UUID
    email: str
    name: str
    is_admin: bool


async def get_user(conn: AsyncConnection, user_id: uuid.UUID) -> UserSummary | None:
    u = schema.users.c
    row = (
        await conn.execute(sa.select(u.id, u.email, u.name, u.is_admin).where(u.id == user_id))
    ).first()
    if row is None:
        return None
    return UserSummary(row.id, str(row.email), row.name, row.is_admin)


@dataclass(frozen=True)
class DemoUserRecord:
    email: str
    name: str
    is_admin: bool
    memberships: list[tuple[str, TeamRole]]


async def list_demo_users(conn: AsyncConnection, limit: int = 50) -> list[DemoUserRecord]:
    """The named demo personas, for the login page's account switcher.

    Personas have no digits in their email (`priya.lead@...`); the generated users of the large
    seed do (`aarav.gupta17@...`). Capped, because the large seed has 2,000 users.
    """
    u, m, t = schema.users.c, schema.memberships.c, schema.teams.c
    people = (
        await conn.execute(
            sa.select(u.id, u.email, u.name, u.is_admin)
            .where(u.deactivated_at.is_(None), u.email.op("!~")("[0-9]@"))
            .order_by(u.is_admin.desc(), u.name)
            .limit(limit)
        )
    ).all()
    rows = await conn.execute(
        sa.select(m.user_id, t.key, m.role)
        .select_from(schema.memberships.join(schema.teams, t.id == m.team_id))
        .where(m.user_id.in_([p.id for p in people]))
        .order_by(t.key)
    )
    by_user: dict[uuid.UUID, list[tuple[str, TeamRole]]] = {}
    for user_id, team_key, role in rows:
        by_user.setdefault(user_id, []).append((team_key, TeamRole(role)))
    return [DemoUserRecord(str(p.email), p.name, p.is_admin, by_user.get(p.id, [])) for p in people]


@dataclass(frozen=True)
class DirectoryEntry:
    id: uuid.UUID
    name: str
    email: str


def _like_pattern(q: str) -> str:
    escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


async def search_directory(
    conn: AsyncConnection,
    q: str | None,
    *,
    limit: int,
    after: tuple[str, uuid.UUID] | None,
) -> list[DirectoryEntry]:
    """People a picker can offer: active users whose name or email contains `q`.

    Keyset page ordered by (name, id), no OFFSET (CLAUDE.md I8). A `%` or `_` typed by the user
    is matched literally, not as a wildcard.
    """
    u = schema.users.c
    query = (
        sa.select(u.id, u.name, u.email)
        .where(u.deactivated_at.is_(None))
        .order_by(u.name, u.id)
        .limit(limit)
    )
    if q:
        pattern = _like_pattern(q)
        query = query.where(
            sa.or_(
                u.name.ilike(pattern, escape="\\"),
                sa.cast(u.email, sa.Text).ilike(pattern, escape="\\"),
            )
        )
    if after is not None:
        query = query.where(sa.tuple_(u.name, u.id) > sa.tuple_(after[0], after[1]))
    rows = await conn.execute(query)
    return [DirectoryEntry(r.id, r.name, str(r.email)) for r in rows]


async def user_exists(conn: AsyncConnection, user_id: uuid.UUID) -> bool:
    u = schema.users.c
    found = (
        await conn.execute(sa.select(u.id).where(u.id == user_id, u.deactivated_at.is_(None)))
    ).first()
    return found is not None
