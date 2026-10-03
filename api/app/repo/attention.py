"""The five sections of GET /me/attention (SPEC 7). Each is one indexed query with a LIMIT, so the
cost does not grow with history; each filters with `visibility_clause()` inside the query (I3),
which `first_items` and `count_up_to` apply, so no section can show or count a hidden item."""

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from app.db import schema
from app.domain.enums import ApprovalStatus, ItemStatus, TeamRole
from app.domain.policy import ActorContext
from app.repo import items as items_repo

wi = schema.work_items.c
SECTION_ITEMS = 10  # items shown per section
COUNT_CAP = 100  # "100+"
QUIET_AFTER = timedelta(hours=72)
_FINISHED = [ItemStatus.RESOLVED.value, ItemStatus.CLOSED.value]
_ACTIVE = [ItemStatus.IN_PROGRESS.value, ItemStatus.BLOCKED.value]  # the going-stale index
_NEVER = sa.literal_column("'infinity'::timestamptz", type_=sa.DateTime(timezone=True))

Keys = Callable[[Any], Sequence[ColumnElement[Any]]]


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    count: int
    items: list[items_repo.ItemRecord]


def _teams(ctx: ActorContext, *roles: TeamRole) -> list[uuid.UUID]:
    return [team for team, role in ctx.roles.items() if role in roles]


def _pending_requested_at(item_id: Any) -> ColumnElement[Any]:
    ap = schema.approvals.c
    return (
        sa.select(ap.requested_at)
        .where(ap.item_id == item_id, ap.status == ApprovalStatus.PENDING.value)
        .scalar_subquery()
    )


def _definitions(ctx: ActorContext, now: datetime) -> list[tuple[str, str, list[Any], Keys]]:
    """(key, title, WHERE clauses, order) of each section, in the order SPEC 7 lists them."""
    me = ctx.user_id
    ap, reads, ev = schema.approvals.c, schema.item_reads.c, schema.item_events.c
    leads, workers = _teams(ctx, TeamRole.LEAD), _teams(ctx, TeamRole.MEMBER, TeamRole.LEAD)

    def never_seen_by(item: Any) -> ColumnElement[bool]:
        marker = (
            sa.select(reads.last_read_event_id)
            .where(reads.user_id == me, reads.item_id == item.id)
            .scalar_subquery()
        )
        newer: ColumnElement[bool] = item.last_event_id > sa.func.coalesce(marker, 0)
        return newer

    return [
        (
            "approvals",
            "Needs your approval",
            [
                wi.team_id.in_(leads),
                wi.status == ItemStatus.AWAITING_APPROVAL.value,
                sa.exists().where(
                    ap.item_id == wi.id,
                    ap.status == ApprovalStatus.PENDING.value,
                    ap.requested_by != me,  # you cannot decide your own request
                ),
            ],
            lambda c: (_pending_requested_at(c.id), c.id),  # oldest request first
        ),
        (
            "urgent",
            "Yours, overdue or urgent",
            [
                wi.assignee_id == me,
                wi.status.notin_(_FINISHED),
                sa.or_(wi.sla_breached_at.is_not(None), wi.priority <= 1),
            ],
            lambda c: (c.priority, sa.func.coalesce(c.due_at, _NEVER), c.id),
        ),
        (
            "unowned",
            "Needs an owner",
            [
                wi.team_id.in_(workers),  # a viewer cannot take it, so it is not theirs to do
                wi.assignee_id.is_(None),
                wi.status == ItemStatus.NEW.value,
            ],
            lambda c: (c.priority, c.created_at, c.id),
        ),
        (
            "quiet",
            "Yours, going quiet",
            [
                wi.assignee_id == me,
                wi.status.in_(_ACTIVE),
                wi.last_activity_at < now - QUIET_AFTER,
            ],
            lambda c: (c.last_activity_at, c.id),  # least recent first
        ),
        (
            "requests",
            "Your requests",
            [
                wi.requester_id == me,
                never_seen_by(wi),
                # Activity by someone else (or the system): your own last action is not news.
                sa.exists().where(ev.id == wi.last_event_id, ev.actor_id.is_distinct_from(me)),
            ],
            lambda c: (c.updated_at.desc(), c.id),
        ),
    ]


async def attention(conn: AsyncConnection, ctx: ActorContext, now: datetime) -> list[Section]:
    sections: list[Section] = []
    for key, title, clauses, keys in _definitions(ctx, now):
        found = await items_repo.first_items(conn, ctx, clauses, keys, SECTION_ITEMS)
        if len(found) < SECTION_ITEMS:
            count = len(found)  # the page is not full, so it is all of them
        else:
            count = await items_repo.count_up_to(conn, ctx, clauses, COUNT_CAP)
        sections.append(Section(key, title, count, found))
    return sections
