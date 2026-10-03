"""SPEC 5.4: five failures lock the account for 15 minutes; the message never reveals which
emails exist."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import time_machine

from tests.integration.conftest import sign_in
from tests.support.seeded import SeededDatabase
from tests.support.users import PASSWORD, create_user, fetch_one

WRONG = "definitely not it"


async def fresh_user(seeded: SeededDatabase) -> str:
    email = f"throttle-{uuid.uuid4().hex[:8]}@baton.test"
    await create_user(seeded.url, email)
    return email


async def state(seeded: SeededDatabase, email: str) -> tuple[int, datetime | None]:
    row = await fetch_one(
        seeded.url, "SELECT failed_logins, locked_until FROM users WHERE email = $1", email
    )
    assert row is not None
    return row["failed_logins"], row["locked_until"]


async def test_the_sixth_attempt_is_429_even_with_the_right_password(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = await fresh_user(seeded)

    for attempt in range(5):
        response = await sign_in(api, email, WRONG)
        assert response.status_code == 401, f"attempt {attempt + 1}"

    sixth = await sign_in(api, email, PASSWORD)  # now the CORRECT password

    assert sixth.status_code == 429
    body = sixth.json()
    assert body["code"] == "RATE_LIMITED"
    assert "Try again in 15 minutes" in body["detail"]
    assert 890 <= int(sixth.headers["Retry-After"]) <= 900
    assert "baton_session" not in sixth.headers.get("set-cookie", "")


async def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = await fresh_user(seeded)

    wrong_password = await sign_in(api, email, WRONG)
    unknown_email = await sign_in(api, f"nobody-{uuid.uuid4().hex[:6]}@baton.test", WRONG)

    assert wrong_password.status_code == unknown_email.status_code == 401
    a, b = wrong_password.json(), unknown_email.json()
    for key in ("type", "title", "status", "detail", "code"):
        assert a[key] == b[key], key
    assert a["detail"] == "Invalid email or password."


async def test_the_lock_expires_after_fifteen_minutes(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = await fresh_user(seeded)
    start = datetime.now(UTC)
    with time_machine.travel(start, tick=False):
        for _ in range(5):
            await sign_in(api, email, WRONG)
        assert (await sign_in(api, email, PASSWORD)).status_code == 429

    with time_machine.travel(start + timedelta(minutes=14, seconds=50), tick=False):
        assert (await sign_in(api, email, PASSWORD)).status_code == 429

    with time_machine.travel(start + timedelta(minutes=15, seconds=1), tick=False):
        assert (await sign_in(api, email, PASSWORD)).status_code == 200


async def test_after_a_lock_expires_one_more_failure_does_not_lock_again(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = await fresh_user(seeded)
    start = datetime.now(UTC)
    with time_machine.travel(start, tick=False):
        for _ in range(5):
            await sign_in(api, email, WRONG)

    with time_machine.travel(start + timedelta(minutes=16), tick=False):
        assert (await sign_in(api, email, WRONG)).status_code == 401
        assert (await sign_in(api, email, PASSWORD)).status_code == 200  # not locked again

    assert await state(seeded, email) == (0, None)


async def test_a_successful_login_resets_the_counter(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = await fresh_user(seeded)
    for _ in range(4):
        await sign_in(api, email, WRONG)
    assert (await state(seeded, email))[0] == 4

    assert (await sign_in(api, email, PASSWORD)).status_code == 200

    assert await state(seeded, email) == (0, None)
    for _ in range(4):  # four more failures: still not locked, the old four were forgotten
        assert (await sign_in(api, email, WRONG)).status_code == 401
    assert (await sign_in(api, email, PASSWORD)).status_code == 200


async def test_the_lock_is_per_account(api: httpx.AsyncClient, seeded: SeededDatabase) -> None:
    locked = await fresh_user(seeded)
    other = await fresh_user(seeded)
    for _ in range(5):
        await sign_in(api, locked, WRONG)

    assert (await sign_in(api, other, PASSWORD)).status_code == 200


async def test_simultaneous_wrong_passwords_are_all_counted(
    app_settings: object, seeded: SeededDatabase
) -> None:
    """The counter is one locked UPDATE, so parallel failures cannot overwrite each other."""
    from app.config import Settings
    from tests.support.app import running_app

    assert isinstance(app_settings, Settings)
    email = await fresh_user(seeded)

    async with running_app(app_settings) as client:
        results = await asyncio.gather(*(sign_in(client, email, WRONG) for _ in range(5)))

    assert [r.status_code for r in results] == [401] * 5
    failures, locked_until = await state(seeded, email)
    assert failures == 5
    assert locked_until is not None


async def test_a_locked_account_does_not_confirm_the_password(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = await fresh_user(seeded)
    for _ in range(5):
        await sign_in(api, email, WRONG)

    right = await sign_in(api, email, PASSWORD)
    wrong = await sign_in(api, email, WRONG)

    assert right.status_code == wrong.status_code == 429
    assert right.json()["detail"] == wrong.json()["detail"]
