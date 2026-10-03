"""Run the real app (lifespan included) against a database, for integration tests."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from app.config import Settings
from app.main import create_app


@asynccontextmanager
async def running_app(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
