"""/healthz says the process is up; /readyz says it can actually serve (SPEC 11)."""

import httpx

from app.config import Settings
from app.db.migrations import expected_head
from app.db.urls import with_database
from tests.support.app import running_app
from tests.support.db import ascratch_database


def settings_for(url: str) -> Settings:
    return Settings(database_url=url, env="test")


def test_expected_head_is_the_newest_migration() -> None:
    assert expected_head() == "0003"


async def test_ready_when_the_database_is_at_the_migration_head(test_database_url: str) -> None:
    async with (
        ascratch_database(test_database_url) as url,
        running_app(settings_for(url)) as client,
    ):
        response = await client.get("/api/v1/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "database": True,
        "migrations": True,
        "current": "0003",
        "expected": "0003",
    }


async def test_not_ready_when_migrations_are_behind(test_database_url: str) -> None:
    async with (
        ascratch_database(test_database_url, migrate_to="0001") as url,
        running_app(settings_for(url)) as client,
    ):
        response = await client.get("/api/v1/readyz")

    body = response.json()
    assert response.status_code == 503
    assert (body["database"], body["migrations"], body["current"]) == (True, False, "0001")


async def test_not_ready_when_the_database_was_never_migrated(test_database_url: str) -> None:
    async with (
        ascratch_database(test_database_url, migrate_to=None) as url,
        running_app(settings_for(url)) as client,
    ):
        response = await client.get("/api/v1/readyz")

    body = response.json()
    assert response.status_code == 503
    assert (body["database"], body["migrations"], body["current"]) == (True, False, None)


async def test_not_ready_when_the_database_is_unreachable(test_database_url: str) -> None:
    unreachable = with_database(test_database_url, "no_such_database_here")
    async with running_app(settings_for(unreachable)) as client:
        response = await client.get("/api/v1/readyz")

    assert response.status_code == 503
    assert response.json()["database"] is False


async def test_healthz_does_not_touch_the_database(test_database_url: str) -> None:
    unreachable = with_database(test_database_url, "no_such_database_here")
    async with running_app(settings_for(unreachable)) as client:
        response = await client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert isinstance(client, httpx.AsyncClient)
