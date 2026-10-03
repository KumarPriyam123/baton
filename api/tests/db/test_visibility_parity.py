"""T-VIS: the SQL visibility clause and the Python can_view must say the same thing (SPEC 5.1).

Runs on the seeded demo database (600 items, 24 people, confidential Compliance items).
"""

import random
import uuid
from collections.abc import AsyncIterator, Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from app.db import schema
from app.domain.enums import ItemStatus, TeamRole
from app.domain.policy import (
    Action,
    ActorContext,
    ItemFacts,
    TeamFacts,
    can,
    can_view,
    visibility_clause,
)
from app.repo.users import load_actor_context
from tests.support.seeded import SeededDatabase, seeded_database

wi = schema.work_items.c


@pytest.fixture(scope="module")
def seeded(test_database_url: str) -> Iterator[SeededDatabase]:
    with seeded_database(test_database_url) as database:
        yield database


@pytest.fixture
async def conn(seeded: SeededDatabase) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(seeded.url)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


async def all_facts(conn: AsyncConnection) -> dict[uuid.UUID, ItemFacts]:
    rows = await conn.execute(
        sa.select(wi.id, wi.team_id, wi.requester_id, wi.assignee_id, wi.confidential, wi.status)
    )
    return {
        row.id: ItemFacts(
            team_id=row.team_id,
            requester_id=row.requester_id,
            assignee_id=row.assignee_id,
            confidential=row.confidential,
            status=ItemStatus(row.status),
        )
        for row in rows
    }


async def visible_ids(conn: AsyncConnection, ctx: ActorContext) -> set[uuid.UUID]:
    rows = await conn.execute(sa.select(wi.id).where(visibility_clause(ctx)))
    return {row.id for row in rows}


async def email_to_id(conn: AsyncConnection) -> dict[str, uuid.UUID]:
    rows = await conn.execute(sa.select(schema.users.c.email, schema.users.c.id))
    return {str(email): user_id for email, user_id in rows}


async def context_for(conn: AsyncConnection, email: str) -> ActorContext:
    ctx = await load_actor_context(conn, (await email_to_id(conn))[email])
    assert ctx is not None
    return ctx


async def test_sql_and_python_agree_for_every_demo_user(conn: AsyncConnection) -> None:
    facts = await all_facts(conn)
    users = await email_to_id(conn)
    assert len(facts) == 600
    assert len(users) == 24

    sizes = set()
    for email, user_id in users.items():
        ctx = await load_actor_context(conn, user_id)
        assert ctx is not None
        expected = {item_id for item_id, f in facts.items() if can_view(ctx, f)}
        actual = await visible_ids(conn, ctx)
        counted = (
            await conn.execute(
                sa.select(sa.func.count())
                .select_from(schema.work_items)
                .where(visibility_clause(ctx))
            )
        ).scalar_one()

        assert actual == expected, f"{email}: SQL and Python disagree"
        assert counted == len(expected), f"{email}: count differs from the list"
        sizes.add(len(expected))

    assert len(sizes) >= 8, "the demo users should see clearly different things"


async def test_sql_and_python_agree_for_random_role_combinations(conn: AsyncConnection) -> None:
    """Role mixes the demo data never produces: a viewer who requested something, an admin who
    leads one team and views another, a lead of every team, no roles at all..."""
    rng = random.Random(7)
    facts = await all_facts(conn)
    users = list(await email_to_id(conn))
    teams = {f.team_id for f in facts.values()}
    user_ids = await email_to_id(conn)
    seen_sizes = set()

    for _ in range(300):
        roles = {
            team: rng.choice([TeamRole.VIEWER, TeamRole.MEMBER, TeamRole.LEAD])
            for team in teams
            if rng.random() < 0.5
        }
        ctx = ActorContext(user_ids[rng.choice(users)], rng.random() < 0.1, roles)
        expected = {item_id for item_id, f in facts.items() if can_view(ctx, f)}

        assert await visible_ids(conn, ctx) == expected
        seen_sizes.add(len(expected))

    assert len(seen_sizes) > 50  # the sample really did exercise different shapes


async def test_a_user_with_no_roles_sees_only_what_they_requested(conn: AsyncConnection) -> None:
    facts = await all_facts(conn)
    user_id = next(iter((await email_to_id(conn)).values()))
    ctx = ActorContext(user_id, False, {})

    assert await visible_ids(conn, ctx) == {
        i for i, f in facts.items() if f.requester_id == user_id
    }


async def test_a_confidential_compliance_item_is_invisible_to_other_members(
    conn: AsyncConnection,
) -> None:
    """Done-when: 404 to a non-lead member of its team, and in no list or count."""
    facts = await all_facts(conn)
    farah = await context_for(conn, "farah@baton.test")  # Compliance member
    ishaan = await context_for(conn, "ishaan.lead@baton.test")  # Compliance lead
    admin = await context_for(conn, "admin@baton.test")
    cmp_team = next(t for t, role in farah.roles.items() if role == TeamRole.MEMBER)

    hidden = [
        (item_id, f)
        for item_id, f in facts.items()
        if f.team_id == cmp_team
        and f.confidential
        and f.assignee_id != farah.user_id
        and f.requester_id != farah.user_id
    ]
    assert len(hidden) >= 5

    farah_sees = await visible_ids(conn, farah)
    for item_id, f in hidden:
        assert item_id not in farah_sees  # not in any list
        assert can(farah, Action.VIEW, f).code == "NOT_FOUND"  # 404, not 403
        assert can(farah, Action.COMMENT, f).code == "NOT_FOUND"
        assert item_id in await visible_ids(conn, ishaan)  # the lead sees it
        assert item_id in await visible_ids(conn, admin)  # so does an admin

    # the same member still sees confidential items she owns or raised
    own = [i for i, f in facts.items() if f.confidential and f.assignee_id == farah.user_id]
    assert own
    assert all(i in farah_sees for i in own)


async def test_the_requester_outside_the_team_sees_their_own_confidential_item(
    conn: AsyncConnection,
) -> None:
    facts = await all_facts(conn)
    found = 0
    for item_id, f in facts.items():
        if not f.confidential:
            continue
        requester = await load_actor_context(conn, f.requester_id)
        assert requester is not None
        if requester.is_admin or f.team_id in requester.roles:
            continue  # only interested in requesters with no role in the item's team
        found += 1
        assert item_id in await visible_ids(conn, requester)
        assert can(requester, Action.VIEW, f).allowed
        if found == 10:
            break

    assert found >= 3


async def test_whoever_may_manage_a_team_can_see_every_item_in_it(conn: AsyncConnection) -> None:
    """Why the membership guard may count a team's items without the visibility clause: the only
    people allowed to manage members (leads of the team, admins) see all of that team's items,
    confidential ones included."""
    facts = await all_facts(conn)
    users = await email_to_id(conn)
    checked = 0
    for user_id in users.values():
        ctx = await load_actor_context(conn, user_id)
        assert ctx is not None
        visible = await visible_ids(conn, ctx)
        for team_id in {f.team_id for f in facts.values()}:
            allowed = can(ctx, Action.MANAGE_MEMBERS, TeamFacts(team_id)).allowed
            if not allowed:
                continue
            checked += 1
            in_team = {i for i, f in facts.items() if f.team_id == team_id}
            assert in_team <= visible, (
                f"{user_id} may manage {team_id} but cannot see all its items"
            )

    assert checked >= 12  # 12 leads x their team, plus the admin x 6 teams


async def test_filtering_happens_before_limit_so_pages_stay_full(conn: AsyncConnection) -> None:
    """The failure mode this guards: filter in Python after LIMIT and get short or leaky pages."""
    for email in ("farah@baton.test", "dev.viewer@baton.test", "meera@baton.test"):
        ctx = await context_for(conn, email)
        total = len(await visible_ids(conn, ctx))
        page = await conn.execute(
            sa.select(wi.id).where(visibility_clause(ctx)).order_by(wi.id).limit(25)
        )

        assert len(page.all()) == min(25, total), email


async def test_the_parity_check_would_catch_a_clause_that_ignores_confidentiality(
    conn: AsyncConnection,
) -> None:
    """Sabotage: a clause that forgets the confidential rule must disagree with can_view."""
    facts = await all_facts(conn)
    farah = await context_for(conn, "farah@baton.test")
    # Right in every respect except that it forgets the confidential rule.
    forgetful = await conn.execute(
        sa.select(wi.id).where(
            sa.or_(wi.requester_id == farah.user_id, wi.team_id.in_(list(farah.roles)))
        )
    )

    wrong = {row.id for row in forgetful}
    expected = {i for i, f in facts.items() if can_view(farah, f)}

    assert wrong != expected
    assert wrong > expected  # it leaks exactly the confidential items
    assert all(facts[i].confidential for i in wrong - expected)
