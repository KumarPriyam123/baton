"""No N+1: the number of SQL statements per request does not grow with the rows it returns."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from sqlalchemy import event

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import sign_in
from tests.integration.test_items_list import Population, population  # noqa: F401
from tests.support.items import ITEMS, created, edited
from tests.support.users import PASSWORD


@asynccontextmanager
async def counting_app(settings: Settings) -> AsyncIterator[tuple[httpx.AsyncClient, list[str]]]:
    """The real app, plus the SQL statements its engine runs (BEGIN/COMMIT are not statements)."""
    app = create_app(settings)
    statements: list[str] = []

    def count(conn: object, cursor: object, statement: str, *rest: object) -> None:
        statements.append(statement)

    async with app.router.lifespan_context(app):
        event.listen(app.state.engine.sync_engine, "before_cursor_execute", count)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, statements


async def statements_for(
    client: httpx.AsyncClient, statements: list[str], path: str
) -> tuple[int, int]:
    before = len(statements)
    response = await client.get(path)
    assert response.status_code == 200, response.text
    return len(statements) - before, len(response.json().get("items", [None]))


async def test_a_list_costs_the_same_number_of_statements_for_five_rows_or_a_hundred(
    population: Population,  # noqa: F811
) -> None:
    settings = Settings(database_url=population.url, env="test")
    async with counting_app(settings) as (client, statements):
        assert (await sign_in(client, population.lead_email, PASSWORD)).status_code == 200

        small, small_rows = await statements_for(client, statements, f"{ITEMS}?limit=5")
        large, large_rows = await statements_for(client, statements, f"{ITEMS}?limit=100")
        filtered, _ = await statements_for(
            client, statements, f"{ITEMS}?team=PAY&status=blocked&status=new&assignee=me&limit=100"
        )

    assert (small_rows, large_rows) == (5, 100)
    assert small == large == filtered
    assert large <= 6  # session, the actor's roles, the page: no per-row queries


async def test_the_detail_of_an_item_is_a_bounded_number_of_statements(
    population: Population,  # noqa: F811
) -> None:
    settings = Settings(database_url=population.url, env="test")
    async with counting_app(settings) as (client, statements):
        assert (await sign_in(client, population.admin_email, PASSWORD)).status_code == 200
        keys = [i["key"] for i in (await client.get(f"{ITEMS}?limit=20")).json()["items"]]

        counts = [(await statements_for(client, statements, f"{ITEMS}/{k}"))[0] for k in keys]

    assert len(set(counts)) == 1, counts  # the same for an item with or without an approval
    assert counts[0] <= 5


async def test_facets_are_one_aggregate_query_whatever_the_filters(
    population: Population,  # noqa: F811
) -> None:
    settings = Settings(database_url=population.url, env="test")
    async with counting_app(settings) as (client, statements):
        assert (await sign_in(client, population.member_email, PASSWORD)).status_code == 200

        plain, _ = await statements_for(client, statements, f"{ITEMS}/facets")
        narrow, _ = await statements_for(client, statements, f"{ITEMS}/facets?team=PAY&priority=0")

    assert plain == narrow
    assert plain <= 5


async def test_an_edit_with_several_events_is_a_bounded_number_of_statements(
    population: Population,  # noqa: F811
) -> None:
    settings = Settings(database_url=population.url, env="test")
    async with counting_app(settings) as (client, statements):
        assert (await sign_in(client, population.lead_email, PASSWORD)).status_code == 200
        item = await created(client, team_key="PAY", title="Budget item")

        before = len(statements)
        await edited(client, item, {"title": "Budget item renamed", "priority": 0})
        used = len(statements) - before

    # session + roles + lock + update + 2 events x 4 writes + the item read-back, plus the command
    # transaction's own SET LOCALs: a fixed number for two events, not a loop over rows.
    assert used <= 25, used
