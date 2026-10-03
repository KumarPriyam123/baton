import uuid
from typing import Annotated, cast

from fastapi import APIRouter, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncConnection

from app.api.constants import API_PREFIX
from app.api.cookies import set_csrf_cookie
from app.api.deps import Conn, CurrentActor, utcnow
from app.api.idempotency import OptionalIdempotencyKey, Outcome, fingerprint, run_idempotent
from app.api.pagination import ITEM_DEFAULT_LIMIT, ITEM_MAX_LIMIT
from app.api.responses import respond
from app.api.schemas.auth import MembershipOut, MeOut, TeamRef, UserOut
from app.api.schemas.collab import (
    AttentionOut,
    AttentionSection,
    MarkedRead,
    MarkReadRequest,
    NotificationOut,
    NotificationsPage,
    SectionKey,
)
from app.api.schemas.items import ItemOut, PersonOut
from app.auth import sessions
from app.db.tx import CommandTx
from app.domain.errors import Unauthenticated
from app.repo import attention as attention_repo
from app.repo import notifications as notifications_repo
from app.repo import users as users_repo
from app.services import items as items_service

router = APIRouter(prefix=API_PREFIX, tags=["me"])


async def build_me(conn: AsyncConnection, user_id: str | uuid.UUID, csrf_token: str) -> MeOut:
    uid = user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(user_id)
    user = await users_repo.get_user(conn, uid)
    if user is None:
        raise Unauthenticated("Sign in to continue.")
    memberships = await users_repo.list_memberships(conn, uid)
    return MeOut(
        user=UserOut(id=user.id, email=user.email, name=user.name, is_admin=user.is_admin),
        memberships=[
            MembershipOut(team=TeamRef(key=m.team_key, name=m.team_name), role=m.role)
            for m in memberships
        ],
        csrf_token=csrf_token,
    )


@router.get("/me")
async def get_me(request: Request, response: Response, actor: CurrentActor, conn: Conn) -> MeOut:
    """Who I am, my teams and roles, and the CSRF token to send with unsafe requests.

    If the CSRF cookie has gone missing (cleared, or a new browser profile with a copied
    session), a new one is issued here: this is where a client recovers from CSRF_FAILED.
    """
    csrf = request.cookies.get(sessions.CSRF_COOKIE)
    if not csrf:
        csrf = sessions.new_csrf_token()
        set_csrf_cookie(response, request, csrf)
    return await build_me(conn, actor.user_id, csrf)


@router.get("/me/attention", operation_id="get_attention", summary="What needs me")
async def get_attention(actor: CurrentActor, conn: Conn) -> AttentionOut:
    """Five sections (SPEC 7), each its own indexed query: the first 10 items and a count capped
    at 100 (show "100+" at 100). Only items you may see are listed or counted."""
    now = utcnow()
    sections = await attention_repo.attention(conn, actor.ctx, now)
    return AttentionOut(
        sections=[
            AttentionSection(
                key=cast(SectionKey, s.key),  # the repo only uses the five section keys
                title=s.title,
                count=s.count,
                items=[
                    ItemOut.from_view(items_service.build_view(r, actor.ctx, now)) for r in s.items
                ],
            )
            for s in sections
        ]
    )


@router.get("/me/notifications", operation_id="list_notifications", summary="My notifications")
async def list_notifications(
    actor: CurrentActor,
    conn: Conn,
    unread: Annotated[bool, Query(description="Only the ones not yet read")] = False,
    cursor: Annotated[int | None, Query(ge=1, description="`next_cursor` of the last page")] = None,
    limit: Annotated[int, Query(ge=1, le=ITEM_MAX_LIMIT)] = ITEM_DEFAULT_LIMIT,
) -> NotificationsPage:
    """Newest first. Notifications about items you can no longer see are left out."""
    found, next_cursor = await notifications_repo.list_notifications(
        conn, actor.ctx, unread_only=unread, before_id=cursor, limit=limit
    )
    return NotificationsPage(
        items=[
            NotificationOut(
                id=r.id,
                kind=r.kind,
                item_key=r.item_key,
                item_title=r.item_title,
                event_id=r.event_id,
                actor=PersonOut(id=r.actor_id, name=r.actor_name or "") if r.actor_id else None,
                created_at=r.created_at,
                read_at=r.read_at,
            )
            for r in found
        ],
        next_cursor=next_cursor,
    )


@router.post(
    "/me/notifications/read", operation_id="mark_notifications_read", summary="Mark as read"
)
async def mark_notifications_read(
    body: MarkReadRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """Mark some of your notifications (`ids`) or all of them (`all: true`) read. Rows that are
    not yours or already read are ignored; `marked` is how many changed."""

    async def work(tx: CommandTx) -> Outcome:
        marked = await notifications_repo.mark_read(
            tx.conn, actor.user_id, None if body.all else body.ids, tx.now
        )
        return Outcome(200, MarkedRead(marked=marked).model_dump(mode="json"))

    outcome = await run_idempotent(
        conn,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
        key=idempotency_key,
        request_fingerprint=await fingerprint(request),
        work=work,
    )
    return respond(outcome, with_etag=False)
