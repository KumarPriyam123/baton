"""GET /items and /items/facets (SPEC 8): filters, sorts and keyset pagination over 1,000 items.

The expected answers are written as plain SQL in this file, independently of the code under test.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any

import asyncpg
import httpx
import pytest

from app.config import Settings
from app.db.urls import asyncpg_dsn
from tests.integration.conftest import sign_in
from tests.support.app import running_app
from tests.support.db import scratch_database
from tests.support.items import ITEMS
from tests.support.users import PASSWORD, create_user

ITEM_COUNT = 1_000

# The visibility rule of SPEC 5.1, spelled out by hand. $1 is the person asking.
VISIBLE = """(
    i.requester_id = $1
    OR EXISTS (
        SELECT 1 FROM memberships m
         WHERE m.team_id = i.team_id AND m.user_id = $1
           AND (NOT i.confidential OR m.role = 'lead' OR i.assignee_id = $1)
    )
)"""

# An admin sees everything; the comparison only keeps $1 in the statement.
ADMIN_VISIBLE = "($1::uuid IS NOT NULL)"

ORDER_BY = {
    "priority": "i.priority, COALESCE(i.due_at, 'infinity'), i.id",
    "due": "COALESCE(i.due_at, 'infinity'), i.id",
    "updated": "i.updated_at DESC, i.id DESC",
    "created": "i.created_at DESC, i.id DESC",
}

# Ties on purpose: 7 distinct created_at values, 5 distinct updated_at values, 20 due dates, and
# 20% of the items have no due date at all.
POPULATE = """
WITH g AS (
    SELECT n, random() r1, random() r2, random() r3, random() r4, random() r5
      FROM generate_series(1, $1::int) n
), shaped AS (
    SELECT g.*,
           CASE WHEN n % 10 < 7 THEN $2::uuid ELSE $3::uuid END AS team_id,
           (ARRAY['new','in_progress','blocked','awaiting_approval','resolved','closed'])
               [1 + floor(r2 * 6)::int] AS status
      FROM g
)
INSERT INTO work_items
    (key, team_id, number, origin_team_id, type, title, description, priority, status,
     resolution, requester_id, assignee_id, confidential, requires_approval, due_at, due_source,
     last_activity_at, version, created_at, updated_at)
SELECT (CASE WHEN s.team_id = $2::uuid THEN 'PAY' ELSE 'SUP' END) || '-' || n,
       s.team_id, n, s.team_id,
       (ARRAY['incident','customer_issue','payment_investigation','engineering',
              'compliance_request','ops_task'])[1 + floor(r1 * 6)::int]::item_type,
       'Item number ' || n,
       '',
       floor(r3 * 4)::int,
       s.status::item_status,
       CASE WHEN s.status IN ('resolved', 'closed') THEN 'done'::resolution END,
       (ARRAY[$4::uuid, $5::uuid, $6::uuid])[1 + n % 3],
       CASE WHEN s.status = 'new' THEN NULL
            WHEN s.team_id = $2::uuid THEN $5::uuid ELSE $6::uuid END,
       r4 < 0.12,
       false,
       CASE WHEN r5 < 0.2 THEN NULL
            ELSE date_trunc('day', now()) + (floor(r5 * 20)::int - 10) * interval '1 day' END,
       'auto',
       now(),
       1,
       now() - floor(r1 * 7)::int * interval '1 day',
       now() - floor(r3 * 5)::int * interval '1 hour'
  FROM shaped s
"""


@dataclass(frozen=True)
class Population:
    url: str
    pay: uuid.UUID
    sup: uuid.UUID
    lead_email: str  # lead of PAY
    member_email: str  # member of PAY
    sup_email: str  # member of SUP
    admin_email: str
    ids: dict[str, uuid.UUID]  # email -> user id


async def _populate(url: str) -> Population:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        pay = await conn.fetchval(
            "INSERT INTO teams (key, name) VALUES ('PAY', 'Payments') RETURNING id"
        )
        sup = await conn.fetchval(
            "INSERT INTO teams (key, name) VALUES ('SUP', 'Support') RETURNING id"
        )
    finally:
        await conn.close()
    emails = {
        "lead": "lead@list.test",
        "member": "member@list.test",
        "sup": "sup@list.test",
        "admin": "admin@list.test",
    }
    ids = {
        emails["lead"]: await create_user(url, emails["lead"], memberships={pay: "lead"}),
        emails["member"]: await create_user(url, emails["member"], memberships={pay: "member"}),
        emails["sup"]: await create_user(url, emails["sup"], memberships={sup: "member"}),
        emails["admin"]: await create_user(url, emails["admin"], is_admin=True),
    }
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        await conn.execute(
            POPULATE,
            ITEM_COUNT,
            pay,
            sup,
            ids[emails["lead"]],
            ids[emails["member"]],
            ids[emails["sup"]],
        )
        await conn.execute("UPDATE teams SET item_seq = $1", ITEM_COUNT)
        await conn.execute("ANALYZE")
    finally:
        await conn.close()
    return Population(
        url, pay, sup, emails["lead"], emails["member"], emails["sup"], emails["admin"], ids
    )


@pytest.fixture(scope="module")
def population(test_database_url: str) -> Iterator[Population]:
    with scratch_database(test_database_url) as url:
        yield asyncio.run(_populate(url))


@pytest.fixture
async def client(population: Population) -> AsyncIterator[httpx.AsyncClient]:
    settings = Settings(database_url=population.url, env="test")
    async with running_app(settings) as http_client:
        yield http_client


async def as_user(client: httpx.AsyncClient, email: str) -> httpx.AsyncClient:
    response = await sign_in(client, email, PASSWORD)
    assert response.status_code == 200, email
    return client


async def expected_keys(
    population: Population, email: str, where: str = "TRUE", order: str = "priority", *args: Any
) -> list[str]:
    """Keys the person should see, in order, straight from SQL."""
    me = population.ids[email]
    visible = ADMIN_VISIBLE if email == population.admin_email else VISIBLE
    conn = await asyncpg.connect(asyncpg_dsn(population.url))
    try:
        found = await conn.fetch(
            f"SELECT i.key FROM work_items i WHERE {visible} AND ({where}) "  # noqa: S608
            f"ORDER BY {ORDER_BY[order]}",
            me,
            *args,
        )
    finally:
        await conn.close()
    return [r["key"] for r in found]


async def walk(client: httpx.AsyncClient, query: str, limit: int) -> list[str]:
    """Follow next_cursor to the end; every page must respect the limit."""
    keys: list[str] = []
    cursor: str | None = None
    pages = 0
    while True:
        params = f"{query}&limit={limit}" + (f"&cursor={cursor}" if cursor else "")
        response = await client.get(f"{ITEMS}?{params}")
        assert response.status_code == 200, response.text
        page = response.json()
        assert len(page["items"]) <= limit
        keys += [i["key"] for i in page["items"]]
        cursor = page["next_cursor"]
        pages += 1
        assert pages < ITEM_COUNT, "the cursor never ends"
        if cursor is None:
            return keys


# ----- pagination -------------------------------------------------------------------------


@pytest.mark.parametrize("sort", ["priority", "due", "updated", "created"])
@pytest.mark.parametrize("limit", [100, 37, 7])
async def test_walking_every_page_returns_each_visible_item_once_in_order_for_every_sort(
    client: httpx.AsyncClient, population: Population, sort: str, limit: int
) -> None:
    await as_user(client, population.lead_email)

    seen = await walk(client, f"sort={sort}", limit)

    assert len(seen) == len(set(seen)), "an item appeared on two pages"
    assert seen == await expected_keys(population, population.lead_email, order=sort)
    assert len(seen) > 600  # most of the 1,000 are the lead's to see


@pytest.mark.parametrize("who", ["member_email", "sup_email", "admin_email"])
async def test_what_a_person_cannot_see_is_never_on_a_page_and_pages_stay_full(
    client: httpx.AsyncClient, population: Population, who: str
) -> None:
    email = getattr(population, who)
    await as_user(client, email)

    seen = await walk(client, "sort=priority", 100)

    assert seen == await expected_keys(population, email)
    # Filtering after LIMIT would leave short pages in the middle; the visibility clause runs
    # before it, so every page but the last is full.
    first = (await client.get(f"{ITEMS}?limit=100")).json()
    assert len(first["items"]) == 100 or first["next_cursor"] is None


async def test_items_without_a_due_date_sort_last_and_are_all_reached(
    client: httpx.AsyncClient, population: Population
) -> None:
    await as_user(client, population.admin_email)

    seen = await walk(client, "sort=due", 50)

    assert len(seen) == ITEM_COUNT
    without = {
        r["key"] for r in await _rows(population, "SELECT key FROM work_items WHERE due_at IS NULL")
    }
    assert len(without) > 100
    assert set(seen[-len(without) :]) == without  # all of them come after every dated item


async def _rows(population: Population, sql: str, *args: Any) -> list[asyncpg.Record]:
    conn = await asyncpg.connect(asyncpg_dsn(population.url))
    try:
        found: list[asyncpg.Record] = await conn.fetch(sql, *args)
        return found
    finally:
        await conn.close()


async def test_a_cursor_from_one_sort_is_refused_by_another(
    client: httpx.AsyncClient, population: Population
) -> None:
    await as_user(client, population.admin_email)
    cursor = (await client.get(f"{ITEMS}?sort=due&limit=5")).json()["next_cursor"]

    response = await client.get(f"{ITEMS}?sort=priority&cursor={cursor}")

    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "cursor"


@pytest.mark.parametrize("cursor", ["not-base64!!", "e30", "eyJzIjoicHJpb3JpdHkiLCJ2IjpbMV19", ""])
async def test_a_garbage_cursor_is_a_400_never_a_500(
    client: httpx.AsyncClient, population: Population, cursor: str
) -> None:
    await as_user(client, population.admin_email)

    response = await client.get(f"{ITEMS}?cursor={cursor}")

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"


async def test_limit_is_capped_at_one_hundred_and_defaults_to_fifty(
    client: httpx.AsyncClient, population: Population
) -> None:
    await as_user(client, population.admin_email)

    default = (await client.get(ITEMS)).json()
    over = await client.get(f"{ITEMS}?limit=101")
    zero = await client.get(f"{ITEMS}?limit=0")

    assert len(default["items"]) == 50
    assert (over.status_code, zero.status_code) == (400, 400)


# ----- filters ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Case:
    name: str
    query: str
    where: str
    args: tuple[Any, ...] = ()


CASES = [
    Case("team", "team=SUP", "i.team_id = (SELECT id FROM teams WHERE key = 'SUP')"),
    Case("team lowercase", "team=pay", "i.team_id = (SELECT id FROM teams WHERE key = 'PAY')"),
    Case("one status", "status=blocked", "i.status = 'blocked'"),
    Case(
        "several statuses",
        "status=new&status=in_progress",
        "i.status IN ('new', 'in_progress')",
    ),
    Case("priority", "priority=0&priority=1", "i.priority IN (0, 1)"),
    Case("type", "type=incident&type=ops_task", "i.type IN ('incident', 'ops_task')"),
    Case("unassigned", "assignee=none", "i.assignee_id IS NULL"),
    Case("mine", "assignee=me", "i.assignee_id = $1"),
    Case("requested by me", "requester=me", "i.requester_id = $1"),
    Case(
        "overdue",
        "overdue=true",
        "i.due_at < now() AND i.status NOT IN ('resolved', 'closed')",
    ),
    Case(
        "not overdue",
        "overdue=false",
        "NOT (COALESCE(i.due_at < now(), false) AND i.status NOT IN ('resolved', 'closed'))",
    ),
    Case("confidential only", "confidential=true", "i.confidential"),
    Case("not confidential", "confidential=false", "NOT i.confidential"),
    Case(
        "updated since",
        "updated_since=" + "{since}",
        "i.updated_at >= now() - interval '3 hours'",
    ),
    Case(
        "everything at once",
        "team=PAY&status=in_progress&status=blocked&priority=0&priority=1&priority=2"
        "&type=incident&type=engineering&type=ops_task&assignee=none&confidential=false",
        "i.team_id = (SELECT id FROM teams WHERE key = 'PAY') "
        "AND i.status IN ('in_progress', 'blocked') AND i.priority IN (0, 1, 2) "
        "AND i.type IN ('incident', 'engineering', 'ops_task') AND i.assignee_id IS NULL "
        "AND NOT i.confidential",
    ),
    Case(
        "assigned to me in my team and open",
        "team=PAY&assignee=me&status=in_progress&status=blocked&status=awaiting_approval",
        "i.team_id = (SELECT id FROM teams WHERE key = 'PAY') AND i.assignee_id = $1 "
        "AND i.status IN ('in_progress', 'blocked', 'awaiting_approval')",
    ),
]


@pytest.mark.parametrize("case", CASES, ids=[c.name for c in CASES])
@pytest.mark.parametrize("who", ["lead_email", "member_email", "sup_email"])
async def test_filters_return_exactly_the_matching_items_the_person_can_see(
    client: httpx.AsyncClient, population: Population, case: Case, who: str
) -> None:
    email = getattr(population, who)
    await as_user(client, email)
    query = case.query
    if "{since}" in query:
        # three hours ago, as a time with a zone
        since = (await _rows(population, "SELECT now() - interval '3 hours' AS t"))[0]["t"]
        query = query.replace("{since}", since.isoformat().replace("+00:00", "Z"))

    seen = await walk(client, query, 100)

    assert seen == await expected_keys(population, email, case.where)


async def test_assignee_can_be_a_specific_person(
    client: httpx.AsyncClient, population: Population
) -> None:
    await as_user(client, population.admin_email)
    member = population.ids[population.member_email]

    seen = await walk(client, f"assignee={member}", 100)

    assert set(seen) == {
        r["key"]
        for r in await _rows(
            population, "SELECT key FROM work_items WHERE assignee_id = $1", member
        )
    }


@pytest.mark.parametrize(
    "query",
    [
        "status=finished",
        "priority=9",
        "type=chore",
        "assignee=somebody",
        "requester=none",
        "overdue=maybe",
        "updated_since=yesterday",
        "updated_since=2025-01-01T00:00:00",
        "sort=random",
        "team=TOOLONG",
        "q=",
        "colour=red",
    ],
)
async def test_unknown_or_malformed_parameters_are_a_400_not_silently_ignored(
    client: httpx.AsyncClient, population: Population, query: str
) -> None:
    await as_user(client, population.admin_email)

    response = await client.get(f"{ITEMS}?{query}")

    assert response.status_code == 400, query
    assert response.json()["code"] == "VALIDATION_FAILED"


async def test_an_unknown_team_key_matches_nothing(
    client: httpx.AsyncClient, population: Population
) -> None:
    await as_user(client, population.admin_email)

    page = (await client.get(f"{ITEMS}?team=ZZZ")).json()

    assert page == {"items": [], "next_cursor": None}


# ----- facets -----------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["lead_email", "member_email", "sup_email", "admin_email"])
@pytest.mark.parametrize("case", [CASES[0], CASES[3], CASES[14]], ids=lambda c: c.name)
async def test_facets_count_exactly_what_the_list_would_return(
    client: httpx.AsyncClient, population: Population, who: str, case: Case
) -> None:
    email = getattr(population, who)
    await as_user(client, email)

    facets = (await client.get(f"{ITEMS}/facets?{case.query}")).json()
    keys = await walk(client, case.query, 100)

    expected = await _rows(
        population,
        f"SELECT i.status::text AS status, i.priority::text AS priority, i.type::text AS type, "  # noqa: S608
        f"t.key AS team FROM work_items i JOIN teams t ON t.id = i.team_id "
        f"WHERE {ADMIN_VISIBLE if email == population.admin_email else VISIBLE} AND ({case.where})",
        population.ids[email],
        *case.args,
    )
    assert len(expected) == len(keys)
    for dimension in ("status", "priority", "type", "team"):
        counts: dict[str, int] = {}
        for row in expected:
            counts[row[dimension]] = counts.get(row[dimension], 0) + 1
        assert facets[dimension] == counts, dimension
        assert sum(facets[dimension].values()) == len(keys)


async def test_facets_reject_list_only_parameters(
    client: httpx.AsyncClient, population: Population
) -> None:
    await as_user(client, population.admin_email)

    response = await client.get(f"{ITEMS}/facets?sort=due")

    assert response.status_code == 400
