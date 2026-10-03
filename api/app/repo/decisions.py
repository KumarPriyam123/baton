"""The decision log (SPEC 7, `GET /decisions`): events with `is_decision = true`, newest first.

Each row joins its item and filters with `visibility_clause()` in the WHERE clause, before the
LIMIT (I3): a decision about a confidential item the person cannot view is neither listed nor paged
past. Visibility is judged against the item as it is now, like notifications. A keyset page by
event id, served by the partial index `item_events_decisions_idx (team_id, id DESC)` when a team
is chosen (I8).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import EventKind
from app.domain.policy import ActorContext, visibility_clause

e = schema.item_events.c
wi = schema.work_items.c

# The kinds that can be decisions (SPEC 3.2 ★), for the `kind` filter.
DECISION_KINDS = (
    EventKind.PRIORITY_CHANGED,
    EventKind.STATUS_CHANGED,
    EventKind.RESOLVED,
    EventKind.REOPENED,
    EventKind.CLOSED,
    EventKind.TRANSFERRED,
    EventKind.APPROVAL_APPROVED,
    EventKind.APPROVAL_REJECTED,
    EventKind.APPROVAL_INVALIDATED,
    EventKind.REQUIRES_APPROVAL_CHANGED,
    EventKind.CONFIDENTIAL_CHANGED,
)


@dataclass(frozen=True)
class DecisionRecord:
    id: int
    kind: str
    reason: str | None
    data: dict[str, Any]
    actor_id: uuid.UUID | None
    actor_name: str | None
    created_at: datetime
    item_key: str
    item_title: str
    team_key: str
    team_name: str


async def list_decisions(
    conn: AsyncConnection,
    ctx: ActorContext,
    *,
    team_key: str | None,
    kinds: list[str],
    before_id: int | None,
    limit: int,
) -> tuple[list[DecisionRecord], int | None]:
    u, t = schema.users.c, schema.teams.c
    query = (
        sa.select(
            e.id,
            e.kind,
            e.reason,
            e.data,
            e.actor_id,
            u.name.label("actor_name"),
            e.created_at,
            wi.key.label("item_key"),
            wi.title.label("item_title"),
            t.key.label("team_key"),
            t.name.label("team_name"),
        )
        .select_from(
            schema.item_events.join(schema.work_items, wi.id == e.item_id)
            .join(schema.teams, t.id == e.team_id)  # the team the item had after the decision
            .outerjoin(schema.users, u.id == e.actor_id)
        )
        .where(e.is_decision.is_(True), visibility_clause(ctx))
        .order_by(e.id.desc())
        .limit(limit + 1)
    )
    if team_key is not None:
        query = query.where(t.key == team_key)
    if kinds:
        query = query.where(e.kind.in_(kinds))
    if before_id is not None:
        query = query.where(e.id < before_id)
    rows = (await conn.execute(query)).all()
    page = [DecisionRecord(**dict(r._mapping)) for r in rows[:limit]]
    return page, page[-1].id if len(rows) > limit else None
