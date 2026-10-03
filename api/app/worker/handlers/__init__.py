"""Job handlers, by outbox topic. Each takes the command transaction and the job payload and must
be idempotent: the outbox delivers at least once (SPEC 9)."""

from collections.abc import Awaitable, Callable
from typing import Any

from app.db.tx import CREATED_TOPIC, OUTBOX_TOPIC, CommandTx
from app.worker.handlers import duplicates, notify

Handler = Callable[[CommandTx, dict[str, Any]], Awaitable[None]]

HANDLERS: dict[str, Handler] = {
    OUTBOX_TOPIC: notify.handle,
    CREATED_TOPIC: duplicates.handle,
}
