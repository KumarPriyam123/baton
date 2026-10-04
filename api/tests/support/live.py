"""A real uvicorn server on a free port, and a reader for its Server-Sent Events.

httpx's ASGI transport buffers a whole response, so a stream that never ends cannot be tested
through it. These tests talk real HTTP over a socket instead.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from typing import Any

import httpx
import uvicorn

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import sign_in


@asynccontextmanager
async def live_server(settings: Settings) -> AsyncIterator[str]:
    """Serve the app (lifespan included) and yield its base URL."""
    config = uvicorn.Config(
        create_app(settings),
        host="127.0.0.1",
        port=0,
        log_level="warning",
        timeout_graceful_shutdown=1,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    while not server.started:
        if task.done():
            task.result()
        await asyncio.sleep(0.02)
    port = server.servers[0].sockets[0].getsockname()[1]
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        await task


@dataclass(frozen=True)
class SseEvent:
    name: str
    data: dict[str, Any]
    id: str | None


class SseReader:
    """Reads `GET /api/v1/stream` in the background into a queue."""

    def __init__(self, client: httpx.AsyncClient, params: dict[str, str] | None = None) -> None:
        self.client = client
        self._params = params
        self._queue: asyncio.Queue[SseEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.comments = 0
        self.headers: httpx.Headers | None = None
        self.status: int | None = None
        self._connected = asyncio.Event()

    async def open(self) -> "SseReader":
        self._task = asyncio.create_task(self._read())
        await asyncio.wait_for(self._connected.wait(), 5)
        return self

    async def close(self) -> None:
        if self._task:
            self._task.cancel()
            with suppress(asyncio.CancelledError, httpx.HTTPError):
                await self._task

    async def _read(self) -> None:
        async with self.client.stream("GET", "/api/v1/stream", params=self._params) as response:
            self.status = response.status_code
            self.headers = response.headers
            if response.status_code != 200:
                self._connected.set()
                return
            name, data, event_id = "message", "", None
            async for line in response.aiter_lines():
                if line.startswith(":"):
                    self.comments += 1
                    if line == ": connected":
                        self._connected.set()  # the server has registered this client
                elif line == "":
                    if data:
                        self._queue.put_nowait(SseEvent(name, json.loads(data), event_id))
                    name, data, event_id = "message", "", None
                elif line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    data = line[5:].strip()
                elif line.startswith("id:"):
                    event_id = line[3:].strip()

    async def next(self, timeout: float = 3.0) -> SseEvent:  # noqa: ASYNC109
        return await asyncio.wait_for(self._queue.get(), timeout)

    def pending(self) -> list[SseEvent]:
        out = []
        while not self._queue.empty():
            out.append(self._queue.get_nowait())
        return out


@asynccontextmanager
async def browser(base_url: str, email: str) -> AsyncIterator[httpx.AsyncClient]:
    """A signed-in client on the live server (cookies and CSRF cookie kept)."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10) as client:
        response = await sign_in(client, email)
        assert response.status_code == 200, email
        yield client
