"""Create users with a known password directly in a test database."""

import uuid
from datetime import datetime

import asyncpg

from app.auth.passwords import hash_password
from app.db.urls import asyncpg_dsn

PASSWORD = "correct horse battery staple"


async def create_user(
    url: str,
    email: str,
    *,
    password: str = PASSWORD,
    name: str = "Test User",
    is_admin: bool = False,
    deactivated_at: datetime | None = None,
    memberships: dict[uuid.UUID, str] | None = None,
) -> uuid.UUID:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        user_id = await conn.fetchval(
            "INSERT INTO users (email, name, password_hash, is_admin, deactivated_at) "
            "VALUES ($1, $2, $3, $4, $5) RETURNING id",
            email,
            name,
            hash_password(password),
            is_admin,
            deactivated_at,
        )
        for team_id, role in (memberships or {}).items():
            await conn.execute(
                "INSERT INTO memberships (team_id, user_id, role) VALUES ($1, $2, $3::team_role)",
                team_id,
                user_id,
                role,
            )
        assert isinstance(user_id, uuid.UUID)
        return user_id
    finally:
        await conn.close()


async def fetch_one(url: str, sql: str, *args: object) -> asyncpg.Record | None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        row: asyncpg.Record | None = await conn.fetchrow(sql, *args)
        return row
    finally:
        await conn.close()


async def execute(url: str, sql: str, *args: object) -> None:
    conn = await asyncpg.connect(asyncpg_dsn(url))
    try:
        await conn.execute(sql, *args)
    finally:
        await conn.close()
