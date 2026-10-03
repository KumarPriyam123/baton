"""GET /demo/users exists only in demo mode (SPEC 11)."""

import httpx

from app.config import Settings
from app.demo import DEMO_PASSWORD
from tests.support.app import running_app


async def test_demo_users_lists_the_personas_and_the_shared_password(
    api: httpx.AsyncClient,
) -> None:
    response = await api.get("/api/v1/demo/users")

    body = response.json()
    emails = [u["email"] for u in body["users"]]
    assert response.status_code == 200
    assert body["password"] == DEMO_PASSWORD
    assert emails[0] == "admin@baton.test"  # admins first
    for expected in ("priya.lead", "asha", "rahul", "meera", "dev.viewer", "farah"):
        assert f"{expected}@baton.test" in emails
    assert len(emails) == 24
    priya = next(u for u in body["users"] if u["email"] == "priya.lead@baton.test")
    assert priya["memberships"] == [{"team_key": "PAY", "role": "lead"}]
    assert "password_hash" not in response.text


async def test_demo_users_needs_no_sign_in(api: httpx.AsyncClient) -> None:
    """It feeds the login page, so it cannot require a session."""
    assert (await api.get("/api/v1/demo/users")).status_code == 200


async def test_demo_users_is_404_when_demo_mode_is_off(app_settings: Settings) -> None:
    off = app_settings.model_copy(update={"demo_mode": False})
    async with running_app(off) as client:
        response = await client.get("/api/v1/demo/users")

    body = response.json()
    assert response.status_code == 404
    assert body["code"] == "NOT_FOUND"
    assert DEMO_PASSWORD not in response.text


async def test_every_listed_demo_account_can_sign_in_with_the_shared_password(
    api: httpx.AsyncClient,
) -> None:
    people = (await api.get("/api/v1/demo/users")).json()["users"]

    for person in people:
        response = await api.post(
            "/api/v1/auth/login", json={"email": person["email"], "password": DEMO_PASSWORD}
        )

        assert response.status_code == 200, person["email"]
