"""Fixtures for tests that go through the real app against a seeded, committed database."""

from collections.abc import AsyncIterator, Iterator

import httpx
import pytest

from app.config import Settings
from app.demo import DEMO_PASSWORD
from tests.support.app import running_app
from tests.support.seeded import SeededDatabase, seeded_database


@pytest.fixture(scope="module")
def seeded(test_database_url: str) -> Iterator[SeededDatabase]:
    with seeded_database(test_database_url) as database:
        yield database


@pytest.fixture
def app_settings(seeded: SeededDatabase) -> Settings:
    return Settings(database_url=seeded.url, env="test", demo_mode=True)


@pytest.fixture
async def api(app_settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    """A signed-out client on the real app (lifespan included). Cookies persist per client."""
    async with running_app(app_settings) as http_client:
        yield http_client


async def sign_in(
    client: httpx.AsyncClient, email: str, password: str = DEMO_PASSWORD
) -> httpx.Response:
    return await client.post("/api/v1/auth/login", json={"email": email, "password": password})


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    token = client.cookies.get("baton_csrf")
    assert token, "sign in first"
    return {"X-CSRF-Token": token}
