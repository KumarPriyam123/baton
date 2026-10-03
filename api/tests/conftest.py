import os
from collections.abc import AsyncIterator

import httpx
import pytest

from app.config import Settings
from app.main import create_app

MISSING_DB_MESSAGE = (
    "TEST_DATABASE_URL is not set. Baton's tests need a real PostgreSQL and never skip "
    "database tests. Run them with:\n"
    "  docker compose -f compose.yaml -f compose.test.yaml run --rm api-test\n"
    "or point TEST_DATABASE_URL at a throwaway database, e.g.\n"
    "  postgresql+asyncpg://baton:baton@localhost:5433/baton_test"
)

# Variables that would change Settings behaviour if they leaked in from the shell.
_SETTINGS_ENV = ("ENV", "DEMO_MODE", "SESSION_TTL_HOURS", "FAULT_NOTIFY_FAIL_RATE", "LOG_LEVEL")


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail the whole run, loudly, when there is no test database (never skip)."""
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.exit(MISSING_DB_MESSAGE, returncode=2)


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _SETTINGS_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def test_database_url() -> str:
    return os.environ["TEST_DATABASE_URL"]


@pytest.fixture
def settings(test_database_url: str) -> Settings:
    return Settings(database_url=test_database_url, env="test")


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client
