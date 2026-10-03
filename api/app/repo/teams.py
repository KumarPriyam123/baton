"""Team and membership queries."""

import uuid
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import ItemStatus, TeamRole

MAX_TEAMS = 500  # a bound, not a page: there are tens of teams, but no query is unbounded


@dataclass(frozen=True)
class TeamRecord:
    id: uuid.UUID
    key: str
    name: str
    description: str


@dataclass(frozen=True)
class MemberRecord:
    user_id: uuid.UUID
    name: str
    email: str
    role: TeamRole


async def list_teams(conn: AsyncConnection) -> list[TeamRecord]:
    t = schema.teams.c
    rows = await conn.execute(
        sa.select(t.id, t.key, t.name, t.description).order_by(t.name).limit(MAX_TEAMS)
    )
    return [TeamRecord(r.id, r.key, r.name, r.description) for r in rows]


async def get_team_by_key(conn: AsyncConnection, key: str) -> TeamRecord | None:
    t = schema.teams.c
    row = (
        await conn.execute(sa.select(t.id, t.key, t.name, t.description).where(t.key == key))
    ).first()
    return TeamRecord(row.id, row.key, row.name, row.description) if row else None


async def list_members(
    conn: AsyncConnection,
    team_id: uuid.UUID,
    *,
    limit: int,
    after: tuple[str, uuid.UUID] | None,
) -> list[MemberRecord]:
    """Keyset page ordered by (name, user id): no OFFSET (CLAUDE.md I8)."""
    m, u = schema.memberships.c, schema.users.c
    query = (
        sa.select(u.id, u.name, u.email, m.role)
        .select_from(schema.memberships.join(schema.users, u.id == m.user_id))
        .where(m.team_id == team_id)
        .order_by(u.name, u.id)
        .limit(limit)
    )
    if after is not None:
        query = query.where(sa.tuple_(u.name, u.id) > sa.tuple_(after[0], after[1]))
    rows = await conn.execute(query)
    return [MemberRecord(r.id, r.name, str(r.email), TeamRole(r.role)) for r in rows]


async def get_role(
    conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID, *, for_update: bool = False
) -> TeamRole | None:
    """`for_update=True` locks the row, for a command that is about to change or delete it."""
    m = schema.memberships.c
    query = sa.select(m.role).where(m.team_id == team_id, m.user_id == user_id)
    if for_update:
        query = query.with_for_update()
    role = (await conn.execute(query)).scalar_one_or_none()
    return TeamRole(role) if role is not None else None


async def share_lock_role(
    conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID
) -> TeamRole | None:
    """The role as it is NOW, with the membership row locked FOR SHARE until the command ends.

    A command that makes someone the owner of an item (claim, assign, reopen to the previous
    owner, transfer keeping the owner) reads the owner's role this way. Removing or demoting that
    person takes the row FOR UPDATE first, so one of the two waits for the other: either the new
    owner is seen as gone, or the removal then finds and unassigns the item (SPEC 4.3b). A
    deadlock between the two is retried by run_command."""
    m = schema.memberships.c
    role = (
        await conn.execute(
            sa.select(m.role)
            .where(m.team_id == team_id, m.user_id == user_id)
            .with_for_update(read=True)
        )
    ).scalar_one_or_none()
    return TeamRole(role) if role is not None else None


async def insert_membership(
    conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID, role: TeamRole
) -> bool:
    """False when the person was already in the team (two adds at once: the second changes
    nothing). The caller then reads the role that won."""
    inserted = await conn.execute(
        pg_insert(schema.memberships)
        .values(team_id=team_id, user_id=user_id, role=role.value)
        .on_conflict_do_nothing(index_elements=["team_id", "user_id"])
        .returning(schema.memberships.c.user_id)
    )
    return inserted.first() is not None


async def update_role(
    conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID, role: TeamRole
) -> None:
    m = schema.memberships
    await conn.execute(
        sa.update(m).where(m.c.team_id == team_id, m.c.user_id == user_id).values(role=role.value)
    )


async def delete_membership(conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID) -> None:
    m = schema.memberships
    await conn.execute(sa.delete(m).where(m.c.team_id == team_id, m.c.user_id == user_id))


async def count_open_items_owned(
    conn: AsyncConnection, team_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    """Open items this person is working in this team (served by work_items_assignee_open_idx).

    Deliberately NOT filtered by visibility_clause() (CLAUDE.md I3): this is an integrity check,
    and a hidden item must still block a removal. It is safe because only a lead of this team or
    an admin may reach it (policy MANAGE_MEMBERS / MANAGE_LEADS), and both can see every item of
    the team. tests/db/test_visibility_parity.py proves that claim.
    """
    wi = schema.work_items.c
    finished = [ItemStatus.RESOLVED.value, ItemStatus.CLOSED.value]
    count = (
        await conn.execute(
            sa.select(sa.func.count()).where(
                wi.team_id == team_id, wi.assignee_id == user_id, wi.status.notin_(finished)
            )
        )
    ).scalar_one()
    return int(count)
