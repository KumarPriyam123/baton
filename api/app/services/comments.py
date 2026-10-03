"""Comments, watching and the read marker (SPEC 6.2, 11). Nothing in here knows about HTTP.

A comment is a command: it locks the item row first (I5), checks who may, writes the comment and
its `commented` event through `record_event` (I1) and makes the author a watcher. It never bumps
the item's version (decision 8), so it cannot make anyone else's `If-Match` stale.

Watching and reading are per-person state, not changes to the item: no event, no version.
"""

from dataclasses import dataclass
from datetime import datetime

from app.db.tx import CommandTx, record_event
from app.domain.enums import EventKind
from app.domain.errors import Forbidden, NotFound
from app.domain.policy import Action, ActorContext, Decision, can
from app.repo import collab as collab_repo
from app.repo import items as items_repo
from app.services import items as items_service
from app.services.items import ItemView


@dataclass(frozen=True)
class CommentResult:
    comment_id: int
    event_id: int
    body: str
    created_at: datetime
    view: ItemView


async def add_comment(tx: CommandTx, ctx: ActorContext, key: str, body: str) -> CommentResult:
    item = await items_service.lock_visible(tx, ctx, key)
    decision = can(ctx, Action.COMMENT, items_service.locked_facts(item))
    if not decision.allowed:
        raise Forbidden(decision.reason)

    comment_id = await collab_repo.insert_comment(tx.conn, item.id, ctx.user_id, body, tx.now)
    await items_repo.add_watcher(tx.conn, item.id, ctx.user_id)
    event_id = await record_event(tx, item, EventKind.COMMENTED, data={"comment_id": comment_id})
    view = await items_service.get_item(tx.conn, ctx, key, tx.now)
    return CommentResult(comment_id, event_id, body, tx.now, view)


async def set_watching(tx: CommandTx, ctx: ActorContext, key: str, *, watching: bool) -> ItemView:
    """PUT and DELETE /watch. Both are naturally idempotent, so they take no key."""
    view = await items_service.get_item(tx.conn, ctx, key, tx.now)  # 404 when hidden
    decision = can(ctx, Action.WATCH, items_service.facts_of(view.record))
    if not decision.allowed:
        raise Forbidden(decision.reason)
    if watching:
        await items_repo.add_watcher(tx.conn, view.record.id, ctx.user_id)
    else:
        await collab_repo.remove_watcher(tx.conn, view.record.id, ctx.user_id)
    return await items_service.get_item(tx.conn, ctx, key, tx.now)


async def mark_read(tx: CommandTx, ctx: ActorContext, key: str) -> ItemView:
    """POST /read: "I have seen everything up to now". 404 for an item the actor cannot see."""
    if not await collab_repo.mark_read(tx.conn, ctx, key, tx.now):
        # Either hidden or missing; an item with no events cannot be read, but none exists.
        raise NotFound(Decision.not_found().reason)
    return await items_service.get_item(tx.conn, ctx, key, tx.now)
