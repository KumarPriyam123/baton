"""`item.event` -> notifications (SPEC 9). For each event of the job: the requester, assignee and
watchers except the actor, and only those who can still view the item. Running it twice adds
nothing (UNIQUE (user_id, event_id), ON CONFLICT DO NOTHING)."""

import uuid
from typing import Any

from app.db.tx import CommandTx, notify_user
from app.domain.enums import ItemStatus
from app.domain.policy import ItemFacts, can_view
from app.repo import notifications as notifications_repo
from app.repo.users import load_actor_context


async def handle(tx: CommandTx, payload: dict[str, Any]) -> None:
    for event_id in payload.get("event_ids", []):
        event = await notifications_repo.event_to_notify(tx.conn, int(event_id))
        if event is None:
            continue
        facts = ItemFacts(
            team_id=event.team_id,
            requester_id=event.requester_id,
            assignee_id=event.assignee_id,
            confidential=event.confidential,
            status=ItemStatus(event.status),
        )
        recipients: list[uuid.UUID] = []
        for user_id in sorted(await notifications_repo.candidate_user_ids(tx.conn, event)):
            ctx = await load_actor_context(tx.conn, user_id)
            if ctx is not None and can_view(ctx, facts):  # the one visibility rule (I3)
                recipients.append(user_id)
        added = await notifications_repo.insert_notifications(tx.conn, event, recipients, tx.now)
        for user_id in added:
            await notify_user(tx.conn, user_id)
