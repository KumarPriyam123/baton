"""Approvals (SPEC 3.2, 4.4). Commands call these inside a command transaction, after the item row
is locked: the lock order is item first, then approvals (SPEC 6.4).

An approval is bound to the content it covers by `subject_hash`; the rules about when it stops
covering it live in `domain/workflow.py`, this module only reads and writes rows.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db import schema
from app.domain.enums import ApprovalStatus
from app.domain.workflow import ApprovalFact

ap = schema.approvals.c

# An item has at most one pending approval and a handful of approved ones in its life; this is a
# bound for the lock query, not a page (I8).
MAX_LIVE = 200


async def live_for_item(conn: AsyncConnection, item_id: uuid.UUID) -> list[ApprovalFact]:
    """The pending and approved approvals of an item, locked FOR UPDATE: they are the ones a
    command may change. Rejected, cancelled and invalidated ones no longer matter."""
    rows = await conn.execute(
        sa.select(ap.id, ap.status, ap.subject_hash, ap.requested_by)
        .where(
            ap.item_id == item_id,
            ap.status.in_([ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]),
        )
        .order_by(ap.requested_at, ap.id)
        .limit(MAX_LIVE)
        .with_for_update()
    )
    return [
        ApprovalFact(r.id, ApprovalStatus(r.status), r.subject_hash, r.requested_by) for r in rows
    ]


async def belongs_to_item(
    conn: AsyncConnection, item_id: uuid.UUID, approval_id: uuid.UUID
) -> bool:
    found = await conn.execute(sa.select(ap.id).where(ap.id == approval_id, ap.item_id == item_id))
    return found.first() is not None


async def insert_pending(
    conn: AsyncConnection,
    *,
    approval_id: uuid.UUID,
    item_id: uuid.UUID,
    requested_by: uuid.UUID,
    requested_at: datetime,
    note: str | None,
    content_hash: str,
) -> None:
    await conn.execute(
        sa.insert(schema.approvals).values(
            id=approval_id,
            item_id=item_id,
            status=ApprovalStatus.PENDING.value,
            requested_by=requested_by,
            requested_at=requested_at,
            request_note=note,
            subject_hash=content_hash,
        )
    )


async def decide(
    conn: AsyncConnection,
    approval_id: uuid.UUID,
    *,
    status: ApprovalStatus,
    decided_by: uuid.UUID,
    decided_at: datetime,
    note: str | None,
) -> None:
    """Approve or reject. The CHECK `approvals_four_eyes` refuses `decided_by = requested_by`
    even if the policy was bypassed; the caller maps that to 403."""
    await conn.execute(
        sa.update(schema.approvals)
        .where(ap.id == approval_id, ap.status == ApprovalStatus.PENDING.value)
        .values(
            status=status.value, decided_by=decided_by, decided_at=decided_at, decision_note=note
        )
    )


async def close_without_decision(
    conn: AsyncConnection,
    approval_ids: Sequence[uuid.UUID],
    *,
    status: ApprovalStatus,
    reason: str | None,
) -> None:
    """Cancel or invalidate. `decided_by` stays NULL: nobody decided, and the four-eyes CHECK
    would refuse a requester cancelling their own request if it were filled in."""
    if not approval_ids:
        return
    await conn.execute(
        sa.update(schema.approvals)
        .where(
            ap.id.in_(list(approval_ids)),
            ap.status.in_([ApprovalStatus.PENDING.value, ApprovalStatus.APPROVED.value]),
        )
        .values(status=status.value, invalidated_reason=reason)
    )
