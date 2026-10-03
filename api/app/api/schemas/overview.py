"""Response models for the management views: the dashboard and the decision log (SPEC 7, 11)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.api.schemas.items import PersonOut, TeamRefOut
from app.domain.enums import EventKind, ItemStatus
from app.repo.decisions import DecisionRecord
from app.repo.stats import TeamFigures


class StatusCounts(BaseModel):
    new: int
    in_progress: int
    blocked: int
    awaiting_approval: int


class PriorityCounts(BaseModel):
    p0: int
    p1: int
    p2: int
    p3: int


class OldestItemOut(BaseModel):
    key: str
    title: str
    priority: int
    status: ItemStatus
    created_at: datetime
    assignee: PersonOut | None


class OwnerLoadOut(BaseModel):
    user: PersonOut
    open: int


class TeamStatsOut(BaseModel):
    team: TeamRefOut
    open: int = Field(description="Items not resolved or closed that you may see.")
    by_status: StatusCounts
    by_priority: PriorityCounts
    overdue: int = Field(description="Open and past the due date (the queue's `overdue=true`).")
    unowned: int = Field(description="Open with no owner (the queue's `assignee=none`).")
    going_quiet: int = Field(description="In progress or blocked, no activity for 72 hours.")
    oldest: list[OldestItemOut] = Field(description="The five oldest open items, oldest first.")
    owners: list[OwnerLoadOut] = Field(description="The busiest owners by open items, at most 8.")
    owners_total: int = Field(description="How many people own at least one open item here.")

    @classmethod
    def from_figures(cls, f: TeamFigures) -> "TeamStatsOut":
        return cls(
            team=TeamRefOut(key=f.key, name=f.name),
            open=f.open,
            by_status=StatusCounts(
                **{s.value: f.by_status.get(s.value, 0) for s in _OPEN_STATUSES}
            ),
            by_priority=PriorityCounts(**{f"p{p}": f.by_priority.get(p, 0) for p in range(4)}),
            overdue=f.overdue,
            unowned=f.unowned,
            going_quiet=f.quiet,
            oldest=[
                OldestItemOut(
                    key=o.key,
                    title=o.title,
                    priority=o.priority,
                    status=ItemStatus(o.status),
                    created_at=o.created_at,
                    assignee=PersonOut(id=o.assignee_id, name=o.assignee_name or "")
                    if o.assignee_id
                    else None,
                )
                for o in f.oldest
            ],
            owners=[
                OwnerLoadOut(user=PersonOut(id=o.user_id, name=o.name), open=o.open)
                for o in f.owners
            ],
            owners_total=f.owners_total,
        )


_OPEN_STATUSES = (
    ItemStatus.NEW,
    ItemStatus.IN_PROGRESS,
    ItemStatus.BLOCKED,
    ItemStatus.AWAITING_APPROVAL,
)


class OrgStats(BaseModel):
    open: int
    overdue: int
    unowned: int
    going_quiet: int
    awaiting_approval: int


class StatsOut(BaseModel):
    generated_at: datetime
    org: OrgStats = Field(description="The sum over the teams listed, so it matches their rows.")
    teams: list[TeamStatsOut]


class DecisionOut(BaseModel):
    id: int = Field(description="The event id.")
    kind: EventKind
    reason: str | None
    data: dict[str, Any]
    actor: PersonOut | None
    created_at: datetime
    item_key: str
    item_title: str
    team: TeamRefOut = Field(description="The team the item had after the decision.")

    @classmethod
    def from_record(cls, r: DecisionRecord) -> "DecisionOut":
        return cls(
            id=r.id,
            kind=EventKind(r.kind),
            reason=r.reason,
            data=r.data,
            actor=PersonOut(id=r.actor_id, name=r.actor_name or "") if r.actor_id else None,
            created_at=r.created_at,
            item_key=r.item_key,
            item_title=r.item_title,
            team=TeamRefOut(key=r.team_key, name=r.team_name),
        )


class DecisionsPage(BaseModel):
    items: list[DecisionOut]
    next_cursor: int | None = Field(description="Pass as `cursor` for the next page, or null.")
