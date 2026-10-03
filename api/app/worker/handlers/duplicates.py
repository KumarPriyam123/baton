"""`item.created` -> possible duplicates (SPEC 9): similar open items of the same team go into
`item_similar` (an upsert) and, the first time any are found, one `duplicate_suggested` event.

Running it twice is safe: the upsert overwrites the same rows, and the event is written only if the
item has none yet. The event carries a count, not the other items' keys, because the timeline is
shown to everyone who can see this item and they may not be allowed to see those."""

import uuid
from typing import Any

from app.db.tx import CommandTx, record_event
from app.domain.enums import EventKind
from app.repo import items as items_repo
from app.repo import search as search_repo


async def handle(tx: CommandTx, payload: dict[str, Any]) -> None:
    item_id = uuid.UUID(str(payload["item_id"]))
    key = await items_repo.key_of_id(tx.conn, item_id)
    if key is None:
        return
    item = await items_repo.lock_by_key(tx.conn, key)  # item row first (I5)
    if item is None:
        return
    found = await search_repo.similar_to(tx.conn, item.id, item.team_id, item.title)
    if not found:
        return
    await search_repo.upsert_similar(tx.conn, item.id, found, tx.now)
    if not await search_repo.has_suggestion_event(tx.conn, item.id):
        await record_event(
            tx,
            item,
            EventKind.DUPLICATE_SUGGESTED,
            data={"count": len(found), "top_score": round(found[0].score, 2)},
        )
