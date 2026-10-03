"""Helpers for tests that create and read items through the real app."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import httpx

from app.config import Settings
from app.db.urls import asyncpg_dsn
from tests.integration.conftest import csrf_headers, sign_in
from tests.support.app import running_app

ITEMS = "/api/v1/items"


def new_key() -> str:
    return uuid.uuid4().hex


@asynccontextmanager
async def signed_in_as(settings: Settings, email: str) -> AsyncIterator[httpx.AsyncClient]:
    """A second (third...) browser on the same app, signed in as someone else."""
    async with running_app(settings) as client:
        response = await sign_in(client, email)
        assert response.status_code == 200, email
        yield client


def create_body(**overrides: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "team_key": "PAY",
        "type": "incident",
        "title": "Refund stuck for order 48213",
        "description": "Customer was charged twice.",
        "priority": 2,
    }
    body.update(overrides)
    return body


async def create_item(
    client: httpx.AsyncClient,
    *,
    key: str | None = None,
    headers: dict[str, str] | None = None,
    **overrides: Any,
) -> httpx.Response:
    return await client.post(
        ITEMS,
        json=create_body(**overrides),
        headers={**csrf_headers(client), "Idempotency-Key": key or new_key(), **(headers or {})},
    )


async def created(client: httpx.AsyncClient, **overrides: Any) -> dict[str, Any]:
    response = await create_item(client, **overrides)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def patch_item(
    client: httpx.AsyncClient,
    item_key: str,
    changes: dict[str, Any],
    *,
    version: int | str | None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    headers = dict(csrf_headers(client))
    if version is not None:
        headers["If-Match"] = f'"{version}"' if isinstance(version, int) else version
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return await client.patch(f"{ITEMS}/{item_key}", json=changes, headers=headers)


async def edited(
    client: httpx.AsyncClient, item: dict[str, Any], changes: dict[str, Any]
) -> dict[str, Any]:
    """PATCH with the item's own version; returns the new item."""
    response = await patch_item(client, item["key"], changes, version=item["version"])
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def rows(url: str, sql: str, *args: Any) -> list[dict[str, Any]]:
    """Run a read-only query on the test database, outside the app."""
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        return [dict(r) for r in await conn.fetch(sql, *args)]
    finally:
        await conn.close()


async def scalar(url: str, sql: str, *args: Any) -> Any:
    found = await rows(url, sql, *args)
    return next(iter(found[0].values()))


async def events_of(url: str, item_key: str) -> list[dict[str, Any]]:
    return await rows(
        url,
        "SELECT e.* FROM item_events e JOIN work_items w ON w.id = e.item_id "
        "WHERE w.key = $1 ORDER BY e.id",
        item_key,
    )
