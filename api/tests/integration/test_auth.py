"""Sign in, /me, sign out, and what a session is allowed to be (SPEC 5.3, 5.4)."""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import time_machine

from app.config import Settings
from app.demo import DEMO_PASSWORD
from tests.integration.conftest import csrf_headers, sign_in
from tests.support.app import running_app
from tests.support.seeded import SeededDatabase
from tests.support.users import PASSWORD, create_user, execute, fetch_one

PRIYA = "priya.lead@baton.test"
SESSION = "baton_session"
CSRF = "baton_csrf"


def set_cookie_headers(response: httpx.Response) -> dict[str, str]:
    return {h.split("=", 1)[0]: h for h in response.headers.get_list("set-cookie")}


async def test_login_sets_an_httponly_session_cookie_and_a_readable_csrf_cookie(
    api: httpx.AsyncClient,
) -> None:
    response = await sign_in(api, PRIYA)

    cookies = set_cookie_headers(response)
    assert response.status_code == 200
    assert "httponly" in cookies[SESSION].lower()
    assert "samesite=lax" in cookies[SESSION].lower()
    assert "httponly" not in cookies[CSRF].lower()  # the page must be able to read it
    assert "samesite=lax" in cookies[CSRF].lower()
    assert "secure" not in cookies[SESSION].lower()  # plain http in this test
    assert response.json()["csrf_token"] == api.cookies.get(CSRF)


async def test_login_returns_the_user_and_memberships_but_no_secrets(
    api: httpx.AsyncClient,
) -> None:
    response = await sign_in(api, PRIYA)

    body = response.json()
    assert body["user"]["email"] == PRIYA
    assert body["user"]["is_admin"] is False
    assert [m["role"] for m in body["memberships"]] == ["lead"]
    assert body["memberships"][0]["team"] == {"key": "PAY", "name": "Payments"}
    assert "password_hash" not in response.text
    token = api.cookies.get(SESSION)
    assert token
    assert token not in response.text  # the token is only in the cookie


async def test_cookies_are_marked_secure_over_https(app_settings: Settings) -> None:
    async with running_app(app_settings) as client:
        response = await client.post(
            "https://test/api/v1/auth/login",
            json={"email": PRIYA, "password": DEMO_PASSWORD},
        )

    cookies = set_cookie_headers(response)
    assert "secure" in cookies[SESSION].lower()
    assert "secure" in cookies[CSRF].lower()


async def test_email_is_case_insensitive_and_trimmed(api: httpx.AsyncClient) -> None:
    response = await sign_in(api, "  PRIYA.Lead@Baton.test ")

    assert response.status_code == 200
    assert response.json()["user"]["email"] == PRIYA


async def test_me_requires_a_session(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/v1/me")

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_me_returns_the_signed_in_user_and_the_csrf_token(api: httpx.AsyncClient) -> None:
    await sign_in(api, PRIYA)

    response = await api.get("/api/v1/me")

    body = response.json()
    assert response.status_code == 200
    assert body["user"]["email"] == PRIYA
    assert body["csrf_token"] == api.cookies.get(CSRF)


async def test_me_reissues_a_missing_csrf_cookie(api: httpx.AsyncClient) -> None:
    """The recovery path for CSRF_FAILED (SPEC 12): refetch /me, retry once."""
    await sign_in(api, PRIYA)
    api.cookies.delete(CSRF)

    response = await api.get("/api/v1/me")

    assert response.status_code == 200
    assert response.json()["csrf_token"] == api.cookies.get(CSRF)
    assert api.cookies.get(CSRF)


async def test_only_the_hash_of_the_token_is_stored(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await sign_in(api, PRIYA)
    token = api.cookies.get(SESSION)
    assert token

    row = await fetch_one(seeded.url, "SELECT id FROM sessions WHERE id = $1", token)
    hashed = await fetch_one(
        seeded.url,
        "SELECT id FROM sessions WHERE id = $1",
        hashlib.sha256(token.encode()).hexdigest(),
    )

    assert row is None  # the token itself is nowhere in the database
    assert hashed is not None


async def test_roles_are_not_in_the_cookie_and_changes_apply_to_the_very_next_request(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    team = await fetch_one(seeded.url, "SELECT id FROM teams WHERE key = 'SRE'")
    assert team is not None
    user = f"role-{uuid.uuid4().hex[:6]}@baton.test"
    user_id = await create_user(seeded.url, user, memberships={team["id"]: "member"})
    await sign_in(api, user, PASSWORD)
    first = await api.get("/api/v1/me")
    assert [m["team"]["key"] for m in first.json()["memberships"]] == ["SRE"]
    assert "member" not in (api.cookies.get(SESSION) or "")

    await execute(seeded.url, "DELETE FROM memberships WHERE user_id = $1", user_id)
    second = await api.get("/api/v1/me")

    assert second.status_code == 200  # still signed in
    assert second.json()["memberships"] == []  # but the role is gone, immediately


async def test_logout_ends_the_session_on_the_server(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await sign_in(api, PRIYA)
    token = api.cookies.get(SESSION)
    assert token
    csrf = csrf_headers(api)

    response = await api.post("/api/v1/auth/logout", headers=csrf)

    assert response.status_code == 204
    assert (
        await fetch_one(
            seeded.url,
            "SELECT 1 FROM sessions WHERE id = $1",
            hashlib.sha256(token.encode()).hexdigest(),
        )
        is None
    )
    # The old token is dead even if someone kept a copy of the cookie.
    api.cookies.set(SESSION, token)
    assert (await api.get("/api/v1/me")).status_code == 401


async def test_logout_when_signed_out_is_not_an_error(api: httpx.AsyncClient) -> None:
    await sign_in(api, PRIYA)
    csrf = csrf_headers(api)
    api.cookies.delete(SESSION)

    response = await api.post("/api/v1/auth/logout", headers=csrf)

    assert response.status_code == 204


async def test_a_deactivated_user_cannot_use_an_existing_session(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = f"leaver-{uuid.uuid4().hex[:6]}@baton.test"
    await create_user(seeded.url, email)
    await sign_in(api, email, PASSWORD)
    assert (await api.get("/api/v1/me")).status_code == 200

    await execute(seeded.url, "UPDATE users SET deactivated_at = now() WHERE email = $1", email)

    response = await api.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_a_deactivated_user_cannot_sign_in(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    email = f"gone-{uuid.uuid4().hex[:6]}@baton.test"
    await create_user(seeded.url, email, deactivated_at=datetime.now(UTC))

    response = await sign_in(api, email, PASSWORD)

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email or password."


async def test_a_session_expires_after_twelve_idle_hours(api: httpx.AsyncClient) -> None:
    start = datetime.now(UTC)
    with time_machine.travel(start, tick=False):
        await sign_in(api, PRIYA)

    with time_machine.travel(start + timedelta(hours=11, minutes=59), tick=False):
        assert (await api.get("/api/v1/me")).status_code == 200

    # 12h01m after login, but only 1m after the last use: idle expiry, so still valid.
    with time_machine.travel(start + timedelta(hours=12, minutes=1), tick=False):
        assert (await api.get("/api/v1/me")).status_code == 200

    # Now leave it alone for more than twelve hours.
    with time_machine.travel(start + timedelta(hours=25), tick=False):
        response = await api.get("/api/v1/me")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHENTICATED"


async def test_an_expired_session_is_refused_with_401(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await sign_in(api, PRIYA)
    await execute(seeded.url, "UPDATE sessions SET expires_at = now() - interval '1 second'")

    response = await api.get("/api/v1/me")

    assert response.status_code == 401


async def test_last_seen_is_refreshed_at_most_once_a_minute(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    start = datetime.now(UTC)
    with time_machine.travel(start, tick=False):
        await sign_in(api, PRIYA)
        await api.get("/api/v1/me")
    token_hash = hashlib.sha256((api.cookies.get(SESSION) or "").encode()).hexdigest()

    async def last_seen() -> datetime:
        row = await fetch_one(
            seeded.url, "SELECT last_seen_at FROM sessions WHERE id = $1", token_hash
        )
        assert row is not None
        seen: datetime = row["last_seen_at"]
        return seen

    first = await last_seen()
    with time_machine.travel(start + timedelta(seconds=30), tick=False):
        await api.get("/api/v1/me")
    assert await last_seen() == first  # too soon: no write

    with time_machine.travel(start + timedelta(seconds=90), tick=False):
        await api.get("/api/v1/me")
    assert await last_seen() == start + timedelta(seconds=90)


async def test_unknown_token_is_401(api: httpx.AsyncClient) -> None:
    api.cookies.set(SESSION, "not-a-real-token")

    response = await api.get("/api/v1/me")

    assert response.status_code == 401


async def test_the_request_log_line_carries_the_user_id(
    api: httpx.AsyncClient, capsys: pytest.CaptureFixture[str]
) -> None:
    await sign_in(api, PRIYA)
    me = (await api.get("/api/v1/me")).json()
    out = capsys.readouterr().out

    lines = [json.loads(x) for x in out.splitlines() if x.startswith("{")]
    me_lines = [x for x in lines if x.get("event") == "request" and x.get("route") == "/api/v1/me"]
    assert me_lines
    assert me_lines[-1]["user_id"] == me["user"]["id"]
