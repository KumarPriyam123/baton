"""Tiny SQL helpers that build valid rows. Tests override only the column they want to break."""

import json
import uuid
from dataclasses import dataclass
from typing import Any

import asyncpg

PASSWORD_HASH = "not-a-real-hash"


@dataclass(frozen=True)
class Team:
    id: uuid.UUID
    key: str


@dataclass(frozen=True)
class World:
    """A small valid universe every test starts from (rolled back after the test)."""

    team: Team
    other_team: Team
    requester: uuid.UUID
    lead: uuid.UUID
    member: uuid.UUID
    item_id: uuid.UUID
    item_key: str
    item_number: int
    event_id: int


async def insert(conn: asyncpg.Connection, table: str, **columns: Any) -> asyncpg.Record:
    """INSERT one row and return it. Table and column names come from test code only."""
    names = ", ".join(columns)
    marks = ", ".join(f"${i}" for i in range(1, len(columns) + 1))
    sql = f"INSERT INTO {table} ({names}) VALUES ({marks}) RETURNING *"  # noqa: S608
    row = await conn.fetchrow(sql, *columns.values())
    assert row is not None
    return row


async def make_user(
    conn: asyncpg.Connection, email: str | None = None, **overrides: Any
) -> uuid.UUID:
    suffix = uuid.uuid4().hex[:8]
    columns: dict[str, Any] = {
        "email": email or f"user-{suffix}@baton.test",
        "name": f"User {suffix}",
        "password_hash": PASSWORD_HASH,
    }
    columns.update(overrides)
    row = await insert(conn, "users", **columns)
    user_id: uuid.UUID = row["id"]
    return user_id


async def make_team(conn: asyncpg.Connection, key: str) -> Team:
    row = await insert(conn, "teams", key=key, name=f"Team {key}")
    return Team(id=row["id"], key=key)


async def next_number(conn: asyncpg.Connection, team: Team) -> int:
    """Item numbers come from the team counter, as in SPEC 6.5."""
    number = await conn.fetchval(
        "UPDATE teams SET item_seq = item_seq + 1 WHERE id = $1 RETURNING item_seq", team.id
    )
    assert isinstance(number, int)
    return number


async def make_item(
    conn: asyncpg.Connection,
    team: Team,
    requester: uuid.UUID,
    **overrides: Any,
) -> asyncpg.Record:
    """A valid `new`, unassigned item unless told otherwise."""
    number = overrides.pop("number", None) or await next_number(conn, team)
    columns: dict[str, Any] = {
        "key": f"{team.key}-{number}",
        "team_id": team.id,
        "number": number,
        "origin_team_id": team.id,
        "type": "incident",
        "title": "Refund stuck for order 48213",
        "description": "Customer was charged twice.",
        "priority": 2,
        "status": "new",
        "requester_id": requester,
        "confidential": False,
        "requires_approval": False,
    }
    columns.update(overrides)
    return await insert(conn, "work_items", **columns)


async def make_event(
    conn: asyncpg.Connection,
    item_id: uuid.UUID,
    team_id: uuid.UUID,
    **overrides: Any,
) -> asyncpg.Record:
    columns: dict[str, Any] = {
        "item_id": item_id,
        "team_id": team_id,
        "kind": "created",
        "item_version": 1,
        "data": json.dumps({}),
    }
    columns.update(overrides)
    return await insert(conn, "item_events", **columns)


async def make_world(conn: asyncpg.Connection) -> World:
    team = await make_team(conn, "PAY")
    other_team = await make_team(conn, "SUP")
    requester = await make_user(conn)
    lead = await make_user(conn)
    member = await make_user(conn)
    await insert(conn, "memberships", team_id=team.id, user_id=lead, role="lead")
    await insert(conn, "memberships", team_id=team.id, user_id=member, role="member")
    item = await make_item(conn, team, requester)
    event = await make_event(conn, item["id"], team.id, actor_id=requester)
    return World(
        team=team,
        other_team=other_team,
        requester=requester,
        lead=lead,
        member=member,
        item_id=item["id"],
        item_key=item["key"],
        item_number=item["number"],
        event_id=event["id"],
    )
