"""The one LISTEN connection of an API process (SPEC 10).

LISTEN needs a session-level connection that stays open, so this is a plain asyncpg connection made
straight to Postgres, never one from the SQLAlchemy pool and never through a transaction-mode
pooler. When it drops, it reconnects with backoff and tells the hub to resync every client, because
notifications sent while it was away are gone.
"""

import asyncio
import json
import random
from contextlib import suppress

import asyncpg
import structlog

from app.db.tx import NOTIFICATIONS_CHANNEL, NOTIFY_CHANNEL
from app.db.urls import asyncpg_dsn
from app.realtime.hub import Hub

log = structlog.get_logger()

MAX_BACKOFF_SECONDS = 15.0
KEEPALIVE_SECONDS = 20.0


class Listener:
    def __init__(self, database_url: str, hub: Hub) -> None:
        self._dsn = asyncpg_dsn(database_url)
        self._hub = hub
        self._task: asyncio.Task[None] | None = None
        self._ready = asyncio.Event()

    async def start(self, *, wait: float = 5.0) -> None:
        """Start listening in the background; return once the first connection works, or after
        `wait` seconds (the API still serves requests while Postgres is unreachable)."""
        self._task = asyncio.create_task(self._run(), name="baton-listener")
        with suppress(TimeoutError):
            await asyncio.wait_for(self._ready.wait(), wait)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        backoff = 0.5
        while True:
            try:
                await self._listen_until_lost()
                backoff = 0.5
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.warning("listener_failed", error=str(error), retry_in=backoff)
            await asyncio.sleep(backoff + random.uniform(0, backoff / 2))  # noqa: S311  (jitter)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)

    async def _listen_until_lost(self) -> None:
        conn = await asyncpg.connect(
            self._dsn, server_settings={"application_name": "baton-listen"}
        )
        lost = asyncio.Event()
        conn.add_termination_listener(lambda _conn: lost.set())
        try:
            await conn.add_listener(NOTIFY_CHANNEL, self._on_notify)
            await conn.add_listener(NOTIFICATIONS_CHANNEL, self._on_notify)
            # After LISTEN, so nothing committed from here on can be missed; earlier ids are
            # reported as "resync" to any client that asks to replay them.
            newest = await conn.fetchval("SELECT coalesce(max(id), 0) FROM item_events")
            self._hub.reset(int(newest))
            self._ready.set()
            log.info("listener_connected", floor=int(newest))
            while not lost.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(lost.wait(), KEEPALIVE_SECONDS)
                if not lost.is_set():
                    await conn.execute("SELECT 1")  # notices a half-open connection
        finally:
            self._ready.clear()
            with suppress(Exception):
                await conn.close(timeout=2)

    def _on_notify(self, _conn: asyncpg.Connection, _pid: int, channel: str, payload: str) -> None:
        try:
            self._hub.publish(channel, json.loads(payload))
        except Exception as error:  # a bad payload must not kill the connection
            log.warning("listener_bad_notice", channel=channel, error=str(error))
