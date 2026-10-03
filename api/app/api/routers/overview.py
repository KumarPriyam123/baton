"""Management views (SPEC 7, 11): the dashboard numbers and the decision log. Both are read-only
and filter with `visibility_clause()` inside their queries, so two people can see different
numbers."""

from typing import Annotated

from fastapi import APIRouter, Query

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor, utcnow
from app.api.pagination import ITEM_DEFAULT_LIMIT, ITEM_MAX_LIMIT
from app.api.schemas.overview import (
    DecisionOut,
    DecisionsPage,
    OrgStats,
    StatsOut,
    TeamStatsOut,
)
from app.domain.enums import EventKind
from app.repo import decisions as decisions_repo
from app.repo import stats as stats_repo

router = APIRouter(prefix=API_PREFIX, tags=["overview"])


@router.get("/stats/teams", operation_id="get_team_stats", summary="Dashboard numbers per team")
async def get_team_stats(actor: CurrentActor, conn: Conn) -> StatsOut:
    """Open items per team by status and priority, overdue, unowned, going quiet, the five oldest
    and the busiest owners. Only items you may see are counted, so a lead and a member of the same
    team can see different figures; each figure equals what the same person can list."""
    now = utcnow()
    teams = [
        TeamStatsOut.from_figures(f) for f in await stats_repo.team_stats(conn, actor.ctx, now)
    ]
    return StatsOut(
        generated_at=now,
        org=OrgStats(
            open=sum(t.open for t in teams),
            overdue=sum(t.overdue for t in teams),
            unowned=sum(t.unowned for t in teams),
            going_quiet=sum(t.going_quiet for t in teams),
            awaiting_approval=sum(t.by_status.awaiting_approval for t in teams),
        ),
        teams=teams,
    )


@router.get("/decisions", operation_id="list_decisions", summary="The decision log")
async def list_decisions(
    actor: CurrentActor,
    conn: Conn,
    team: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2,5}$", description="Team key")] = None,
    kind: Annotated[
        list[EventKind] | None, Query(description="Repeat to match any of several decision kinds")
    ] = None,
    cursor: Annotated[int | None, Query(ge=1, description="`next_cursor` of the last page")] = None,
    limit: Annotated[int, Query(ge=1, le=ITEM_MAX_LIMIT)] = ITEM_DEFAULT_LIMIT,
) -> DecisionsPage:
    """Events marked as decisions with their reasons, newest first: why was this decided?
    Decisions about items you cannot see are left out."""
    found, next_cursor = await decisions_repo.list_decisions(
        conn,
        actor.ctx,
        team_key=team.upper() if team else None,
        kinds=[k.value for k in kind or []],
        before_id=cursor,
        limit=limit,
    )
    return DecisionsPage(items=[DecisionOut.from_record(r) for r in found], next_cursor=next_cursor)
