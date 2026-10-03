"""Helpers for tests that drive the workflow endpoints through the real app."""

import uuid
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

import httpx

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import csrf_headers, sign_in
from tests.support.items import ITEMS, rows, scalar
from tests.support.users import create_user

# Demo accounts (all `@baton.test`): PAY has two leads and two members; Meera raises requests.
PRIYA = "priya.lead@baton.test"  # lead of PAY
VIKRAM = "vikram.lead@baton.test"  # the other lead of PAY
ASHA = "asha@baton.test"  # member of PAY
RAHUL = "rahul@baton.test"  # member of PAY and SRE
MEERA = "meera@baton.test"  # member of SUP, viewer on PAY
DEV_VIEWER = "dev.viewer@baton.test"  # viewer on PAY and SRE
NEHA = "neha.lead@baton.test"  # lead of SRE
ISHAAN = "ishaan.lead@baton.test"  # lead of CMP
ADMIN = "admin@baton.test"


async def act(
    client: httpx.AsyncClient,
    item_key: str,
    action: str,
    body: dict[str, Any] | None = None,
    *,
    version: int | str | None = None,
    idempotency_key: str | None = None,
) -> httpx.Response:
    """POST /items/{key}/{action} as the client's user. `version` becomes `If-Match`."""
    headers = dict(csrf_headers(client))
    if version is not None:
        headers["If-Match"] = f'"{version}"' if isinstance(version, int) else version
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    return await client.post(f"{ITEMS}/{item_key}/{action}", json=body or {}, headers=headers)


async def transition(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    action: str,
    *,
    version: int | str | None = None,
    **body: Any,
) -> httpx.Response:
    return await act(
        client,
        item["key"],
        "transition",
        {"action": action, **body},
        version=item["version"] if version is None else version,
    )


async def get(client: httpx.AsyncClient, item_key: str) -> dict[str, Any]:
    response = await client.get(f"{ITEMS}/{item_key}")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def ok(response: httpx.Response, status: int = 200) -> dict[str, Any]:
    assert response.status_code == status, response.text
    body: dict[str, Any] = response.json()
    return body


async def claimed(client: httpx.AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
    return await ok(await act(client, item["key"], "claim"))


async def requested_approval(
    client: httpx.AsyncClient, item: dict[str, Any], note: str | None = None
) -> dict[str, Any]:
    body = {"note": note} if note else {}
    return await ok(
        await act(client, item["key"], "approvals", body, version=item["version"]), status=201
    )


async def decide(
    client: httpx.AsyncClient,
    item: dict[str, Any],
    decision: str,
    *,
    note: str | None = None,
    version: int | str | None = None,
) -> httpx.Response:
    return await act(
        client,
        item["key"],
        f"approvals/{item['approval']['id']}/decision",
        {"decision": decision, **({"note": note} if note else {})},
        version=item["version"] if version is None else version,
    )


async def events(url: str, item_key: str) -> list[dict[str, Any]]:
    return await rows(
        url,
        "SELECT e.id, e.kind, e.item_version, e.team_id, e.actor_id, e.reason, e.is_decision, "
        "e.data::text AS data FROM item_events e JOIN work_items w ON w.id = e.item_id "
        "WHERE w.key = $1 ORDER BY e.id",
        item_key,
    )


async def row(url: str, item_key: str) -> dict[str, Any]:
    found = await rows(url, "SELECT * FROM work_items WHERE key = $1", item_key)
    return found[0]


async def team_id(url: str, key: str) -> uuid.UUID:
    found = await scalar(url, "SELECT id FROM teams WHERE key = $1", key)
    assert isinstance(found, uuid.UUID)
    return found


async def extra_workers(url: str, team: str, count: int, role: str = "member") -> list[str]:
    """New users in a team, with the shared test password. Returns their emails."""
    tid = await team_id(url, team)
    emails = []
    for n in range(count):
        email = f"worker.{uuid.uuid4().hex[:10]}.{n}@baton.test"
        await create_user(url, email, name=f"Worker {n}", memberships={tid: role})
        emails.append(email)
    return emails


@asynccontextmanager
async def many_clients(
    settings: Settings, emails: list[str], password: str
) -> AsyncIterator[list[httpx.AsyncClient]]:
    """One app, one signed-in browser per email: the requests of a race all go to the same
    process, each on a connection of its own."""
    app = create_app(settings)
    async with app.router.lifespan_context(app), AsyncExitStack() as stack:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        clients = [
            await stack.enter_async_context(
                httpx.AsyncClient(transport=transport, base_url="http://test")
            )
            for _ in emails
        ]
        for client, email in zip(clients, emails, strict=True):
            response = await sign_in(client, email, password)
            assert response.status_code == 200, (email, response.text)
        yield clients
