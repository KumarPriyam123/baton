"""Per-team numbers for the dashboard (SPEC 7, `GET /stats/teams`).

Every query filters with `visibility_clause()` in its WHERE clause (I3), so a person's figures are
exactly the items they could list: the same open-item rule as `GET /items?status=...`, the same
overdue rule as `overdue=true` (due date passed, not finished), the same 72 h as "going quiet" in
the inbox. A bounded number of statements whatever the data (four), none per team or per item.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import ItemStatus
from app.domain.policy import ActorContext, visibility_clause
from app.repo.attention import QUIET_AFTER

wi = schema.work_items.c
OLDEST_PER_TEAM = 5
OWNERS_PER_TEAM = 8

_FINISHED = [ItemStatus.RESOLVED.value, ItemStatus.CLOSED.value]
_ACTIVE = [ItemStatus.IN_PROGRESS.value, ItemStatus.BLOCKED.value]


@dataclass(frozen=True)
class OldestItem:
    key: str
    title: str
    priority: int
    status: str
    created_at: datetime
    assignee_id: uuid.UUID | None
    assignee_name: str | None


@dataclass(frozen=True)
class OwnerLoad:
    user_id: uuid.UUID
    name: str
    open: int


@dataclass
class TeamFigures:
    team_id: uuid.UUID
    key: str
    name: str
    open: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    by_priority: dict[int, int] = field(default_factory=dict)
    overdue: int = 0
    unowned: int = 0
    quiet: int = 0
    oldest: list[OldestItem] = field(default_factory=list)
    owners: list[OwnerLoad] = field(default_factory=list)
    owners_total: int = 0


async def team_stats(conn: AsyncConnection, ctx: ActorContext, now: datetime) -> list[TeamFigures]:
    """One entry per team the person has a stake in: teams they belong to (all teams for an admin)
    plus any team where they can see an item (a requester's own requests). Open items only."""
    t, u = schema.teams.c, schema.users.c
    is_open = wi.status.notin_(_FINISHED)

    def count_if(condition: sa.ColumnElement[bool]) -> sa.ColumnElement[int]:
        return sa.func.count().filter(condition)

    counts = (
        sa.select(
            wi.team_id,
            sa.func.count().label("open"),
            *[count_if(wi.status == s.value).label(s.value) for s in _OPEN_STATUSES],
            *[count_if(wi.priority == p).label(f"p{p}") for p in range(4)],
            count_if(wi.due_at < now).label("overdue"),
            count_if(wi.assignee_id.is_(None)).label("unowned"),
            count_if(
                sa.and_(wi.status.in_(_ACTIVE), wi.last_activity_at < now - QUIET_AFTER)
            ).label("quiet"),
        )
        .where(is_open, visibility_clause(ctx))
        .group_by(wi.team_id)
    )
    figures = {r.team_id: r for r in (await conn.execute(counts)).all()}

    team_query = sa.select(t.id, t.key, t.name).order_by(t.name, t.id)
    if not ctx.is_admin:
        team_query = team_query.where(t.id.in_({*ctx.roles, *figures}))
    teams = {
        r.id: TeamFigures(team_id=r.id, key=r.key, name=r.name)
        for r in (await conn.execute(team_query)).all()
    }
    if not teams:
        return []

    for team_id, row in figures.items():
        team = teams[team_id]
        team.open = row.open
        team.by_status = {s.value: getattr(row, s.value) for s in _OPEN_STATUSES}
        team.by_priority = {p: getattr(row, f"p{p}") for p in range(4)}
        team.overdue, team.unowned, team.quiet = row.overdue, row.unowned, row.quiet

    team_ids = list(teams)

    # The five oldest open items of each team: a LATERAL subquery so each team costs one index
    # probe and a top-5 sort, not a window over every open item.
    oldest = (
        sa.select(
            wi.id, wi.key, wi.title, wi.priority, wi.status, wi.created_at, wi.assignee_id, u.name
        )
        .select_from(schema.work_items.outerjoin(schema.users, u.id == wi.assignee_id))
        .where(wi.team_id == t.id, is_open, visibility_clause(ctx))
        .order_by(wi.created_at, wi.id)
        .limit(OLDEST_PER_TEAM)
        .lateral("oldest")
    )
    rows = await conn.execute(
        sa.select(
            t.id.label("team_id"),
            oldest.c.key,
            oldest.c.title,
            oldest.c.priority,
            oldest.c.status,
            oldest.c.created_at,
            oldest.c.assignee_id,
            oldest.c.name.label("assignee_name"),
        )
        .select_from(schema.teams.join(oldest, sa.true()))
        .where(t.id.in_(team_ids))
        .order_by(t.id, oldest.c.created_at, oldest.c.id)
    )
    for r in rows:
        teams[r.team_id].oldest.append(
            OldestItem(
                r.key, r.title, r.priority, r.status, r.created_at, r.assignee_id, r.assignee_name
            )
        )

    # Load per owner: open items per assignee, the busiest few per team.
    per_owner = (
        sa.select(
            wi.team_id,
            wi.assignee_id,
            sa.func.count().label("open"),
        )
        .where(is_open, wi.assignee_id.is_not(None), visibility_clause(ctx))
        .group_by(wi.team_id, wi.assignee_id)
        .subquery("per_owner")
    )
    ranked = sa.select(
        per_owner.c.team_id,
        per_owner.c.assignee_id,
        per_owner.c.open,
        sa.func.row_number()
        .over(
            partition_by=per_owner.c.team_id,
            order_by=(per_owner.c.open.desc(), per_owner.c.assignee_id),
        )
        .label("rank"),
        sa.func.count().over(partition_by=per_owner.c.team_id).label("owners_total"),
    ).subquery("ranked")
    rows = await conn.execute(
        sa.select(
            ranked.c.team_id, ranked.c.assignee_id, ranked.c.open, ranked.c.owners_total, u.name
        )
        .select_from(ranked.join(schema.users, u.id == ranked.c.assignee_id))
        .where(ranked.c.rank <= OWNERS_PER_TEAM)
        .order_by(ranked.c.team_id, ranked.c.rank)
    )
    for r in rows:
        team = teams[r.team_id]
        team.owners.append(OwnerLoad(r.assignee_id, r.name, r.open))
        team.owners_total = r.owners_total
    return list(teams.values())


# The statuses a team can be "open" in, in workflow order (SPEC 4).
_OPEN_STATUSES = (
    ItemStatus.NEW,
    ItemStatus.IN_PROGRESS,
    ItemStatus.BLOCKED,
    ItemStatus.AWAITING_APPROVAL,
)
