"""Small builders shared by the seed tests."""

import random
import uuid
from datetime import UTC, datetime, timedelta

from app.domain.enums import EventKind, ItemStatus
from scripts.seedlib.sim import ItemSim, TeamCtx

T0 = datetime(2026, 9, 1, 9, 0, tzinfo=UTC)


def uid(n: int) -> uuid.UUID:
    return uuid.UUID(int=n)


def make_team(key: str = "PAY", n: int = 100) -> TeamCtx:
    team = TeamCtx(id=uid(n), key=key, name=key)
    team.leads = [uid(n + 1), uid(n + 2)]
    team.members = [uid(n + 3), uid(n + 4)]
    team.viewers = [uid(n + 5)]
    team.finish()
    return team


LEAD_A, LEAD_B, MEMBER_A, MEMBER_B = uid(101), uid(102), uid(103), uid(104)
REQUESTER = uid(1)


def make_item(
    team: TeamCtx | None = None, *, requires_approval: bool = False, priority: int = 2
) -> ItemSim:
    return ItemSim(
        random.Random(1),
        team=team or make_team(),
        item_type="payment_investigation",
        title="Refund stuck for order 48213",
        description="Customer was charged twice.",
        priority=priority,
        requester=REQUESTER,
        confidential=False,
        requires_approval=requires_approval,
        created_at=T0,
    )


def at(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def kinds(item: ItemSim, version: int) -> list[EventKind]:
    return [e.kind for e in item.events if e.version == version]


def status_of(item: ItemSim) -> ItemStatus:
    """The item's current status, read through a call so type narrowing cannot go stale."""
    return item.status
