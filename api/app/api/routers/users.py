from typing import Annotated

from fastapi import APIRouter, Query

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor
from app.api.pagination import DEFAULT_LIMIT, MAX_LIMIT, decode_cursor, encode_cursor
from app.api.schemas.teams import DirectoryEntryOut, DirectoryPage
from app.repo import users as users_repo

router = APIRouter(prefix=f"{API_PREFIX}/users", tags=["users"])


@router.get("")
async def search_users(
    actor: CurrentActor,
    conn: Conn,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    cursor: str | None = None,
) -> DirectoryPage:
    """The directory behind assignee and member pickers: active people, matched by name or email."""
    after = decode_cursor(cursor) if cursor else None
    rows = await users_repo.search_directory(
        conn, q.strip() if q else None, limit=limit + 1, after=after
    )
    page, more = rows[:limit], len(rows) > limit
    return DirectoryPage(
        items=[DirectoryEntryOut(id=r.id, name=r.name, email=r.email) for r in page],
        next_cursor=encode_cursor(page[-1].name, page[-1].id) if more else None,
    )
