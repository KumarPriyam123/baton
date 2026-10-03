"""Search and duplicate suggestions (SPEC 8). SQL building blocks over `work_items`.

The visibility rule is part of every query that uses these (`filter_clauses` in repo/items.py
adds it to the same WHERE clause as the match), so search can never rank, return or count an item
the actor cannot see (I3).

Matching, in the order the SPEC gives it:
- a `q` shaped like a key (`PAY-142`) finds that key exactly, and it is listed first;
- otherwise `websearch_to_tsquery('english', q)` over the generated `search` column (title and key
  weigh more than the description), ranked by `ts_rank`;
- plus trigram word similarity on the title, so a typo ("refnd") still finds "refund".
"""

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection
from sqlalchemy.sql.elements import ColumnElement

from app.db import schema
from app.domain.enums import ItemStatus
from app.domain.policy import ActorContext, visibility_clause

wi = schema.work_items.c

TOP = 50  # ranked search has no deep paging, by design (SPEC 8)
SIMILAR_MIN_SCORE = 0.35
SIMILAR_LIMIT = 5
_KEY_SHAPE = re.compile(r"^[A-Za-z]{2,5}-\d{1,9}$")
_FINISHED = [ItemStatus.RESOLVED.value, ItemStatus.CLOSED.value]


def key_of(q: str) -> str | None:
    """The item key `q` names, if it looks like one."""
    return q.upper() if _KEY_SHAPE.fullmatch(q) else None


def _tsquery(q: str) -> ColumnElement[Any]:
    return sa.func.websearch_to_tsquery(sa.literal_column("'english'::regconfig"), sa.literal(q))


def match_clause(q: str) -> ColumnElement[bool]:
    """Which items `q` finds. Both text tests are GIN-indexed (search, title trigram)."""
    found: list[ColumnElement[bool]] = [
        wi.search.op("@@", return_type=sa.Boolean)(_tsquery(q)),
        sa.literal(q, sa.Text).op("<%", return_type=sa.Boolean)(wi.title),
    ]
    key = key_of(q)
    if key is not None:
        found.append(wi.key == key)
    return sa.or_(*found)


def rank_columns(q: str) -> tuple[ColumnElement[Any], ColumnElement[Any]]:
    """(is the exact key, text score). Order by the first, then the second, both descending."""
    exact = sa.case((wi.key == (key_of(q) or ""), 1), else_=0)
    score = sa.func.ts_rank(wi.search, _tsquery(q)) + sa.func.word_similarity(q, wi.title)
    return exact, score


# ----- possible duplicates while typing (SPEC 8) ----------------------------------------------


@dataclass(frozen=True)
class SimilarRecord:
    id: uuid.UUID
    key: str
    title: str
    status: str
    priority: int
    score: float


async def similar_items(
    conn: AsyncConnection, ctx: ActorContext, team_id: uuid.UUID, title: str
) -> Sequence[SimilarRecord]:
    """Open items of one team whose title is close to `title`: trigram similarity above 0.35, the
    top 5. `%` lets the GIN index find candidates (its threshold is 0.3); the explicit 0.35 is the
    SPEC's. Visibility is in the WHERE clause, so a hidden item is never suggested."""
    score = sa.func.similarity(wi.title, title)
    rows = await conn.execute(
        sa.select(wi.id, wi.key, wi.title, wi.status, wi.priority, score.label("score"))
        .where(
            wi.team_id == team_id,
            wi.status.notin_(_FINISHED),
            wi.title.op("%", return_type=sa.Boolean)(title),
            score > SIMILAR_MIN_SCORE,
            visibility_clause(ctx),
        )
        .order_by(score.desc(), wi.id)
        .limit(SIMILAR_LIMIT)
    )
    return [
        SimilarRecord(r.id, r.key, r.title, str(r.status), int(r.priority), float(r.score))
        for r in rows
    ]
