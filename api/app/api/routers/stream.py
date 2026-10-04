"""GET /api/v1/stream: Server-Sent Events (SPEC 10).

The handler holds no database connection while it streams: it authenticates on a short-lived one,
then waits on the client's queue. Events carry `{key, version, event_id}` only.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse

from app.api.constants import API_PREFIX
from app.api.deps import Actor, current_actor
from app.domain.errors import Unauthenticated
from app.realtime.hub import Client, Hub, ItemNotice, Message, NotificationNudge
from app.repo import notifications as notifications_repo

router = APIRouter(prefix=API_PREFIX, tags=["live"])

HEARTBEAT_SECONDS = 15.0
UNREAD_CAP = 21  # the bell shows "20+": one more than its cap is enough
RETRY_MS = 3_000


def frame(event: str, data: dict[str, Any], event_id: int | None = None) -> str:
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


async def _refresh_context(request: Request, client: Client) -> None:
    """Reload the viewer's roles (every 60 s). False when the session or user is gone."""
    async with request.app.state.engine.connect() as conn:
        actor = await current_actor(request, conn, request.app.state.settings)
    client.refreshed(actor.ctx)


async def _render(request: Request, client: Client, message: Message) -> str:
    if isinstance(message, ItemNotice):
        return frame(
            "item.changed",
            {"key": message.item_key, "version": message.version, "event_id": message.event_id},
            message.event_id,
        )
    if isinstance(message, NotificationNudge):
        async with request.app.state.engine.connect() as conn:
            unread = await notifications_repo.unread_count(conn, client.ctx, cap=UNREAD_CAP)
        return frame("notification.created", {"unread": unread})
    return frame("resync", {})


async def _events(
    request: Request, hub: Hub, client: Client, last_event_id: int | None
) -> AsyncIterator[str]:
    try:
        yield f"retry: {RETRY_MS}\n: connected\n\n"
        if last_event_id is not None:
            for missed in hub.replay(client.ctx, last_event_id):
                yield await _render(request, client, missed)
        while True:
            waited: Message | None
            try:
                waited = await asyncio.wait_for(client.queue.get(), HEARTBEAT_SECONDS)
            except TimeoutError:
                yield ": heartbeat\n\n"
                waited = None
            if client.needs_new_context():
                try:
                    await _refresh_context(request, client)
                except Unauthenticated:
                    return  # session ended or user deactivated; the client reconnects and gets 401
            if waited is not None:
                yield await _render(request, client, waited)
    finally:
        hub.disconnect(client)


@router.get(
    "/stream",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream(
    request: Request,
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    last_event_id_query: Annotated[int | None, Query(alias="last_event_id", ge=0)] = None,
) -> StreamingResponse:
    """Live nudges: `item.changed`, `notification.created`, `resync`, heartbeats every 15 s."""
    async with request.app.state.engine.connect() as conn:
        actor: Actor = await current_actor(request, conn, request.app.state.settings)
    hub: Hub = request.app.state.hub
    last: int | None = last_event_id_query
    if last is None and last_event_id_header and last_event_id_header.isdigit():
        last = int(last_event_id_header)
    client = hub.connect(actor.ctx)
    return StreamingResponse(
        _events(request, hub, client, last),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
