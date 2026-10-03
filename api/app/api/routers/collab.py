"""Collaboration endpoints on an item (SPEC 11): comments, watch, read."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response
from fastapi.responses import JSONResponse

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor
from app.api.idempotency import Outcome, RequiredIdempotencyKey, fingerprint, run_idempotent
from app.api.preconditions import etag
from app.api.responses import respond
from app.api.schemas.collab import CommentCreated, CommentOut, CommentRequest
from app.api.schemas.items import ItemOut, PersonOut
from app.db.tx import CommandTx, run_command
from app.services import comments as comments_service
from app.services.items import ItemView

router = APIRouter(prefix=f"{API_PREFIX}/items", tags=["collaboration"])

_PROBLEMS: dict[int | str, dict[str, object]] = {
    400: {"description": "VALIDATION_FAILED"},
    401: {"description": "UNAUTHENTICATED"},
    403: {"description": "FORBIDDEN or CSRF_FAILED"},
    404: {"description": "NOT_FOUND (missing, or not visible to you)"},
}

ItemKey = Annotated[str, Path(description="Item key, like PAY-142 (not case sensitive)")]
Run = Callable[[CommandTx], Awaitable[ItemView]]


@router.post(
    "/{key}/comments",
    status_code=201,
    response_model=CommentCreated,
    operation_id="add_item_comment",
    summary="Add a comment",
    responses={**_PROBLEMS, 422: {"description": "IDEMPOTENCY_KEY_REUSED"}},
)
async def add_item_comment(
    key: ItemKey,
    body: CommentRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: RequiredIdempotencyKey,
) -> Response:
    """Append a comment. Comments cannot be edited or deleted. The author starts watching the
    item. It writes a `commented` event but does not change the item's `version`, so nobody's
    `If-Match` goes stale. Repeating the request with the same `Idempotency-Key` returns the
    first answer."""

    async def work(tx: CommandTx) -> Outcome:
        result = await comments_service.add_comment(tx, actor.ctx, key.upper(), body.body)
        created = CommentCreated(
            comment=CommentOut(
                id=result.comment_id,
                event_id=result.event_id,
                author=PersonOut(id=actor.user_id, name=actor.session.name),
                body=result.body,
                created_at=result.created_at,
            ),
            item=ItemOut.from_view(result.view),
        )
        return Outcome(201, created.model_dump(mode="json"))

    outcome = await run_idempotent(
        conn,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
        key=idempotency_key,
        request_fingerprint=await fingerprint(request),
        work=work,
    )
    return respond(outcome, with_etag=False)


async def _run(conn: Conn, actor: CurrentActor, request: Request, work: Run) -> Response:
    """Watch and read are naturally idempotent (SPEC 11 lists no key), so they are one plain
    command transaction each."""
    view = await run_command(
        conn,
        work,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
    )
    response = JSONResponse(ItemOut.from_view(view).model_dump(mode="json"))
    response.headers["ETag"] = etag(view.record.version)
    return response


@router.put(
    "/{key}/watch",
    response_model=ItemOut,
    operation_id="watch_item",
    summary="Start watching",
    responses=_PROBLEMS,
)
async def watch_item(key: ItemKey, request: Request, actor: CurrentActor, conn: Conn) -> Response:
    """Follow the item. Doing it twice changes nothing. Watching never changes the version."""
    return await _run(
        conn,
        actor,
        request,
        lambda tx: comments_service.set_watching(tx, actor.ctx, key.upper(), watching=True),
    )


@router.delete(
    "/{key}/watch",
    response_model=ItemOut,
    operation_id="unwatch_item",
    summary="Stop watching",
    responses=_PROBLEMS,
)
async def unwatch_item(key: ItemKey, request: Request, actor: CurrentActor, conn: Conn) -> Response:
    return await _run(
        conn,
        actor,
        request,
        lambda tx: comments_service.set_watching(tx, actor.ctx, key.upper(), watching=False),
    )


@router.post(
    "/{key}/read",
    response_model=ItemOut,
    operation_id="mark_item_read",
    summary="I have seen everything up to now",
    responses=_PROBLEMS,
)
async def mark_item_read(
    key: ItemKey, request: Request, actor: CurrentActor, conn: Conn
) -> Response:
    """Record the item's newest event as seen. `unread_since_event_id` is null in the answer."""
    return await _run(
        conn, actor, request, lambda tx: comments_service.mark_read(tx, actor.ctx, key.upper())
    )
