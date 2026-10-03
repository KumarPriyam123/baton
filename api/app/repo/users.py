"""User and membership queries."""

import uuid

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
