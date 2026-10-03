"""Item endpoints (SPEC 11): create, read, edit, list, facets, history."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor, utcnow
from app.api.idempotency import (
    OptionalIdempotencyKey,
    Outcome,
    RequiredIdempotencyKey,
    fingerprint,
    run_idempotent,
)
from app.api.pagination import ITEM_DEFAULT_LIMIT, ITEM_MAX_LIMIT
from app.api.preconditions import ExpectedVersion, etag
from app.api.problem import PROBLEM_JSON
from app.api.schemas.items import (
    CreateItemRequest,
    EventOut,
    EventsPage,
    FacetsOut,
    ItemOut,
    ItemsPage,
    PatchItemRequest,
)
from app.db.tx import CommandTx
from app.domain.enums import ItemStatus, ItemType
from app.domain.errors import ValidationFailed, VersionConflict
from app.repo import items as items_repo
from app.services import items as items_service

router = APIRouter(prefix=f"{API_PREFIX}/items", tags=["items"])

# The 4xx every command can answer, for the OpenAPI document (bodies are problem+json, SPEC 12).
_PROBLEMS: dict[int | str, dict[str, object]] = {
    400: {"description": "VALIDATION_FAILED"},
    401: {"description": "UNAUTHENTICATED"},
    403: {"description": "FORBIDDEN or CSRF_FAILED"},
    404: {"description": "NOT_FOUND (missing, or not visible to you)"},
}

SORTS = tuple(items_repo.SORTS)
Sort = Annotated[
    str,
    Query(pattern="^(" + "|".join(SORTS) + ")$", description="priority | due | updated | created"),
]

ALLOWED_LIST_PARAMS = frozenset(
    {
        "team",
        "status",
        "priority",
        "type",
        "assignee",
        "requester",
        "overdue",
        "confidential",
        "updated_since",
        "sort",
        "cursor",
        "limit",
    }
)
FILTER_PARAMS = ALLOWED_LIST_PARAMS - {"sort", "cursor", "limit"}


def _respond(outcome: Outcome, *, location: bool = False) -> JSONResponse:
    """The stored (or fresh) answer as an HTTP response. Headers that depend on the body, ETag
    and Location, are rebuilt from it, so a replay carries them too."""
    headers: dict[str, str] = {}
    if outcome.replayed:
        headers["Idempotent-Replayed"] = "true"
    if outcome.is_error:
        return JSONResponse(
            outcome.body, status_code=outcome.status, media_type=PROBLEM_JSON, headers=headers
        )
    headers["ETag"] = etag(outcome.body["version"])
    if location:
        headers["Location"] = f"{API_PREFIX}/items/{outcome.body['key']}"
    return JSONResponse(outcome.body, status_code=outcome.status, headers=headers)


def _assignee_or_requester(
    raw: str | None, field: str, *, allow_none: bool
) -> str | uuid.UUID | None:
    if raw is None:
        return None
    if raw == "me" or (allow_none and raw == "none"):
        return raw
    try:
        return uuid.UUID(raw)
    except ValueError as error:
        allowed = "me, none or a user id" if allow_none else "me or a user id"
        raise ValidationFailed(
            f"{field} must be {allowed}.",
            errors=[{"field": field, "message": f"Use {allowed}.", "type": "value_error"}],
        ) from error


def item_filters(
    request: Request,
    team: Annotated[str | None, Query(pattern=r"^[A-Za-z]{2,5}$", description="Team key")] = None,
    status: Annotated[
        list[ItemStatus] | None, Query(description="Repeat to match any of several")
    ] = None,
    priority: Annotated[list[Annotated[int, Field(ge=0, le=3)]] | None, Query()] = None,
    type: Annotated[list[ItemType] | None, Query()] = None,
    assignee: Annotated[str | None, Query(description="me | none | a user id")] = None,
    requester: Annotated[str | None, Query(description="me | a user id")] = None,
    overdue: Annotated[bool | None, Query(description="Open and past its due date")] = None,
    confidential: bool | None = None,
    updated_since: Annotated[str | None, Query(description="ISO 8601 time")] = None,
) -> items_repo.ItemFilters:
    """Shared by the list and the facets, so both count the same set. Unknown parameters are an
    error rather than being ignored: a filter that is silently dropped shows the wrong items."""
    allowed = ALLOWED_LIST_PARAMS if request.url.path.endswith("/items") else FILTER_PARAMS
    unknown = sorted(set(request.query_params) - allowed)
    if unknown:
        raise ValidationFailed(
            f"Unknown parameter: {', '.join(unknown)}.",
            errors=[
                {"field": u, "message": "Unknown parameter.", "type": "extra"} for u in unknown
            ],
        )
    since = None
    if updated_since is not None:
        try:
            since = datetime.fromisoformat(updated_since)
        except ValueError as error:
            raise ValidationFailed(
                "updated_since must be an ISO 8601 time.",
                errors=[
                    {"field": "updated_since", "message": "Not a time.", "type": "value_error"}
                ],
            ) from error
        if since.tzinfo is None:
            raise ValidationFailed(
                "updated_since needs a time zone, like 2025-01-31T09:00:00Z.",
                errors=[
                    {"field": "updated_since", "message": "No time zone.", "type": "value_error"}
                ],
            )
    return items_repo.ItemFilters(
        team=team.upper() if team else None,
        status=status or [],
        priority=priority or [],
        type=type or [],
        assignee=_assignee_or_requester(assignee, "assignee", allow_none=True),
        requester=_assignee_or_requester(requester, "requester", allow_none=False),
        overdue=overdue,
        confidential=confidential,
        updated_since=since,
    )


Filters = Annotated[items_repo.ItemFilters, Depends(item_filters)]


# ----- create -------------------------------------------------------------------------------


@router.post(
    "",
    status_code=201,
    response_model=ItemOut,
    operation_id="create_item",
    summary="Raise a request",
    responses={**_PROBLEMS, 422: {"description": "IDEMPOTENCY_KEY_REUSED"}},
)
async def create_item(
    body: CreateItemRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    key: RequiredIdempotencyKey,
) -> Response:
    """Create an item in any team (SPEC A2). The number comes from the team's counter; type
    defaults and the due date are applied here. Repeating the request with the same
    `Idempotency-Key` returns the first answer, marked `Idempotent-Replayed: true`."""
    command = items_service.CreateItem(
        team_key=body.team_key,
        type=body.type,
        title=body.title,
        description=body.description,
        priority=body.priority,
        requires_approval=body.requires_approval,
    )

    async def work(tx: CommandTx) -> Outcome:
        view = await items_service.create_item(tx, actor.ctx, command)
        return Outcome(201, ItemOut.from_view(view).model_dump(mode="json"))

    outcome = await run_idempotent(
        conn,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
        key=key,
        request_fingerprint=await fingerprint(request),
        work=work,
    )
    return _respond(outcome, location=True)


# ----- list, facets -------------------------------------------------------------------------


@router.get("", operation_id="list_items", summary="List and filter items", responses=_PROBLEMS)
async def list_items(
    actor: CurrentActor,
    conn: Conn,
    filters: Filters,
    sort: Sort = "priority",
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=ITEM_MAX_LIMIT)] = ITEM_DEFAULT_LIMIT,
) -> ItemsPage:
    """Items you can see, filtered and sorted, a keyset page at a time (no totals, no OFFSET).
    Pass `next_cursor` back as `cursor` with the same filters and sort."""
    now = utcnow()
    records, next_cursor = await items_repo.list_items(
        conn, actor.ctx, filters, sort=sort, cursor=cursor, limit=limit, now=now
    )
    return ItemsPage(
        items=[ItemOut.from_view(items_service.build_view(r, actor.ctx, now)) for r in records],
        next_cursor=next_cursor,
    )


@router.get(
    "/facets",
    operation_id="get_item_facets",
    summary="Counts for the same filters",
    responses=_PROBLEMS,
)
async def get_item_facets(actor: CurrentActor, conn: Conn, filters: Filters) -> FacetsOut:
    counts = await items_repo.facet_counts(conn, actor.ctx, filters, utcnow())
    return FacetsOut(**counts)


# ----- one item -----------------------------------------------------------------------------


@router.get("/{key}", operation_id="get_item", summary="Read an item", responses=_PROBLEMS)
async def get_item(key: str, response: Response, actor: CurrentActor, conn: Conn) -> ItemOut:
    """The item with what you may do (`allowed_actions`) and what comes next. The `ETag` is the
    version to send back as `If-Match` when you edit."""
    view = await items_service.get_item(conn, actor.ctx, key.upper(), utcnow())
    response.headers["ETag"] = etag(view.record.version)
    return ItemOut.from_view(view)


@router.patch(
    "/{key}",
    response_model=ItemOut,
    operation_id="update_item",
    summary="Edit fields",
    responses={
        **_PROBLEMS,
        409: {"description": "WORKFLOW_VIOLATION"},
        412: {"description": "VERSION_CONFLICT, with `current` and `changes_since`"},
        422: {"description": "REASON_REQUIRED or IDEMPOTENCY_KEY_REUSED"},
        428: {"description": "PRECONDITION_REQUIRED: send If-Match"},
    },
)
async def update_item(
    key: str,
    body: PatchItemRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    expected_version: ExpectedVersion,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """Change some fields of an item (SPEC 4.2). `If-Match` must carry the version you saw; if
    the item moved on, the answer is 412 with `current` and `changes_since`."""
    item_key = key.upper()
    command = items_service.EditItem(
        fields=body.fields(), reason=body.reason, apply_type_defaults=body.apply_type_defaults
    )

    async def work(tx: CommandTx) -> Outcome:
        try:
            result = await items_service.edit_item(
                tx, actor.ctx, item_key, expected_version, command
            )
        except items_service.StaleVersion as stale:
            raise VersionConflict(
                "This item changed since you opened it.",
                current=ItemOut.from_view(stale.current).model_dump(mode="json"),
                changes_since=[
                    EventOut.from_record(e).model_dump(mode="json") for e in stale.changes_since
                ],
            ) from stale
        return Outcome(200, ItemOut.from_view(result.view).model_dump(mode="json"))

    outcome = await run_idempotent(
        conn,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
        key=idempotency_key,
        request_fingerprint=await fingerprint(request),
        work=work,
    )
    return _respond(outcome)


# ----- history ------------------------------------------------------------------------------


@router.get(
    "/{key}/events",
    operation_id="list_item_events",
    summary="History of an item",
    responses=_PROBLEMS,
)
async def list_item_events(
    key: str,
    actor: CurrentActor,
    conn: Conn,
    after_event_id: Annotated[
        int, Query(ge=0, description="Continue after this event id (0 = from the start)")
    ] = 0,
    order: Annotated[str, Query(pattern="^(asc|desc)$")] = "asc",
    limit: Annotated[int, Query(ge=1, le=ITEM_MAX_LIMIT)] = ITEM_DEFAULT_LIMIT,
) -> EventsPage:
    """Events by id, oldest first (`asc`) or newest first (`desc`). Walk a page at a time by
    passing `next_cursor` back as `after_event_id`; for `desc` that means "older than"."""
    records, next_after = await items_service.list_events(
        conn,
        actor.ctx,
        key.upper(),
        after_id=after_event_id,
        descending=order == "desc",
        limit=limit,
    )
    return EventsPage(items=[EventOut.from_record(e) for e in records], next_cursor=next_after)
