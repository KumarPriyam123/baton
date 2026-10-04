"""Fan-out of committed changes to connected browsers (SPEC 10).

The listener hands every NOTIFY to `Hub.publish`; the hub copies it into each client's bounded queue
after asking `can_view` about that client. Only `{key, version, event_id}` ever reaches the wire:
the browser refetches through the normal authorized API, so a stale or wrong visibility decision
here can at worst send a "something changed" nudge, never content.

Bounded everywhere: a client that does not read gets its backlog dropped and one `resync`, and the
replay buffer keeps the last `BUFFER_SIZE` events.
"""

import asyncio
import uuid
from collections import deque
from dataclasses import dataclass, field
from time import monotonic

from app.db.tx import NOTIFICATIONS_CHANNEL, NOTIFY_CHANNEL
from app.domain.enums import ItemStatus
from app.domain.policy import ActorContext, ItemFacts, can_view

QUEUE_SIZE = 1_000
BUFFER_SIZE = 500
CONTEXT_TTL_SECONDS = 60.0


@dataclass(frozen=True)
class ItemNotice:
    """One committed item event, with the facts visibility depends on (never sent as is)."""

    event_id: int
    item_key: str
    version: int
    team_id: uuid.UUID
    prev_team_id: uuid.UUID | None
    requester_id: uuid.UUID
    assignee_id: uuid.UUID | None
    confidential: bool

    @staticmethod
    def parse(payload: dict[str, object]) -> "ItemNotice":
        def opt(name: str) -> uuid.UUID | None:
            value = payload.get(name)
            return uuid.UUID(str(value)) if value else None

        return ItemNotice(
            event_id=int(str(payload["event_id"])),
            item_key=str(payload["item_key"]),
            version=int(str(payload["version"])),
            team_id=uuid.UUID(str(payload["team_id"])),
            prev_team_id=opt("prev_team_id"),
            requester_id=uuid.UUID(str(payload["requester_id"])),
            assignee_id=opt("assignee_id"),
            confidential=bool(payload["confidential"]),
        )

    def visible_to(self, ctx: ActorContext) -> bool:
        """After the change, or (for a transfer) before it: whoever just lost the item still hears
        that it changed, and their refetch answers 404."""
        now = ItemFacts(
            self.team_id, self.requester_id, self.assignee_id, self.confidential, ItemStatus.NEW
        )
        if can_view(ctx, now):
            return True
        if self.prev_team_id is None:
            return False
        before = ItemFacts(
            self.prev_team_id,
            self.requester_id,
            self.assignee_id,
            self.confidential,
            ItemStatus.NEW,
        )
        return can_view(ctx, before)


@dataclass(frozen=True)
class Resync:
    """Tell the client to refetch everything it shows."""


@dataclass(frozen=True)
class NotificationNudge:
    """The user has a new notification; the stream turns it into the current unread count."""


Message = ItemNotice | Resync | NotificationNudge


@dataclass(eq=False)
class Client:
    user_id: uuid.UUID
    ctx: ActorContext
    queue: asyncio.Queue[Message]
    ctx_loaded_at: float = field(default_factory=monotonic)

    def needs_new_context(self) -> bool:
        return monotonic() - self.ctx_loaded_at >= CONTEXT_TTL_SECONDS

    def refreshed(self, ctx: ActorContext) -> None:
        self.ctx = ctx
        self.ctx_loaded_at = monotonic()


class Hub:
    def __init__(self, *, queue_size: int = QUEUE_SIZE, buffer_size: int = BUFFER_SIZE) -> None:
        self._queue_size = queue_size
        self._clients: set[Client] = set()
        self._buffer: deque[ItemNotice] = deque(maxlen=buffer_size)
        # Events with an id at or below this are not in the buffer (evicted, or before we listened).
        self._floor: int = 0

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def connect(self, ctx: ActorContext) -> Client:
        client = Client(ctx.user_id, ctx, asyncio.Queue(self._queue_size))
        self._clients.add(client)
        return client

    def disconnect(self, client: Client) -> None:
        self._clients.discard(client)

    def reset(self, floor: int) -> None:
        """The listener (re)connected: anything may have been missed, so every client resyncs and
        replay starts from `floor`, the newest event id at that moment."""
        self._buffer.clear()
        self._floor = floor
        for client in self._clients:
            self._deliver(client, Resync())

    def publish(self, channel: str, payload: dict[str, object]) -> None:
        """Called from the listener's callback, on the event loop: must not block or await."""
        if channel == NOTIFY_CHANNEL:
            notice = ItemNotice.parse(payload)
            if len(self._buffer) == self._buffer.maxlen and self._buffer:
                self._floor = max(self._floor, self._buffer[0].event_id)
            self._buffer.append(notice)
            for client in self._clients:
                if notice.visible_to(client.ctx):
                    self._deliver(client, notice)
        elif channel == NOTIFICATIONS_CHANNEL:
            user_id = uuid.UUID(str(payload["user_id"]))
            for client in self._clients:
                if client.user_id == user_id:
                    self._deliver(client, NotificationNudge())

    def replay(self, ctx: ActorContext, last_event_id: int) -> list[Message]:
        """What a reconnecting client missed, or a single `resync` when the buffer cannot say."""
        if last_event_id < self._floor:
            return [Resync()]
        return [n for n in self._buffer if n.event_id > last_event_id and n.visible_to(ctx)]

    def _deliver(self, client: Client, message: Message) -> None:
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            # Drop the backlog; one resync replaces everything the client has not read.
            while not client.queue.empty():
                client.queue.get_nowait()
            client.queue.put_nowait(Resync())
