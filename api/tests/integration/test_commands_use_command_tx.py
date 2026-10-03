"""CLAUDE.md I5: login, logout and membership commands run on command_tx (timeouts, retry)."""

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

import httpx
import pytest

from app.config import Settings
from app.db import tx
from tests.integration.conftest import csrf_headers, sign_in
from tests.integration.test_teams_members import PRIYA, new_user, role_of, signed_in
from tests.support.app import running_app
from tests.support.seeded import SeededDatabase


@pytest.fixture
def command_tx_entries(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """One entry per command transaction started; the real command_tx still runs."""
    entries: list[int] = []
    real: Callable[..., AbstractAsyncContextManager[tx.CommandTx]] = tx.command_tx

    def counting(*args: object, **kwargs: object) -> AbstractAsyncContextManager[tx.CommandTx]:
        entries.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(tx, "command_tx", counting)
    return entries


async def test_a_successful_login_runs_in_a_command_transaction(
    api: httpx.AsyncClient, command_tx_entries: list[int]
) -> None:
    assert (await sign_in(api, PRIYA)).status_code == 200

    assert len(command_tx_entries) == 1


async def test_a_failed_login_counts_the_failure_in_a_command_transaction(
    api: httpx.AsyncClient, command_tx_entries: list[int], seeded: SeededDatabase
) -> None:
    email, _ = await new_user(seeded)

    response = await sign_in(api, email, "wrong password")

    assert response.status_code == 401
    assert len(command_tx_entries) == 1


async def test_logout_runs_in_a_command_transaction(
    api: httpx.AsyncClient, command_tx_entries: list[int]
) -> None:
    await signed_in(api, PRIYA)
    before = len(command_tx_entries)

    response = await api.post("/api/v1/auth/logout", headers=csrf_headers(api))

    assert response.status_code == 204
    assert len(command_tx_entries) == before + 1


async def test_add_change_and_remove_member_each_run_in_a_command_transaction(
    api: httpx.AsyncClient, command_tx_entries: list[int], seeded: SeededDatabase
) -> None:
    await signed_in(api, PRIYA)
    _, person = await new_user(seeded)
    pay = "/api/v1/teams/PAY/members"
    before = len(command_tx_entries)

    added = await api.post(
        pay, json={"user_id": str(person), "role": "viewer"}, headers=csrf_headers(api)
    )
    changed = await api.patch(f"{pay}/{person}", json={"role": "member"}, headers=csrf_headers(api))
    removed = await api.delete(f"{pay}/{person}", headers=csrf_headers(api))

    assert (added.status_code, changed.status_code, removed.status_code) == (201, 200, 204)
    assert len(command_tx_entries) == before + 3


async def test_two_identical_adds_at_once_give_one_201_and_one_200_never_a_500(
    app_settings: Settings, seeded: SeededDatabase
) -> None:
    _, person = await new_user(seeded)
    body = {"user_id": str(person), "role": "member"}
    async with running_app(app_settings) as first, running_app(app_settings) as second:
        await signed_in(first, PRIYA)
        await signed_in(second, PRIYA)

        results = await asyncio.gather(
            *(
                client.post("/api/v1/teams/PAY/members", json=body, headers=csrf_headers(client))
                for client in (first, second)
            )
        )

    assert sorted(r.status_code for r in results) == [200, 201]
    assert await role_of(seeded, person, "PAY") == "member"
