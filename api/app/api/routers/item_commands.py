"""Workflow endpoints (SPEC 11): claim, release, assign, transition, transfer, approvals.

Each one is a thin shell: parse, run the service as one idempotent command, answer with the item.
The rules are in domain/workflow.py and the order of checks is in services/commands.py.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Path, Request, Response

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor
from app.api.idempotency import OptionalIdempotencyKey, Outcome, fingerprint, run_idempotent
from app.api.preconditions import ExpectedVersion
from app.api.responses import respond
from app.api.schemas.commands import (
    ApprovalRequestBody,
    AssignRequest,
    DecisionRequest,
    TransferRequest,
    TransitionRequest,
)
from app.api.schemas.items import EventOut, ItemOut
from app.db.tx import CommandTx
from app.domain.errors import AlreadyClaimed, VersionConflict
from app.domain.policy import Action
from app.services import commands
from app.services import items as items_service

router = APIRouter(prefix=f"{API_PREFIX}/items", tags=["workflow"])

_PROBLEMS: dict[int | str, dict[str, object]] = {
    400: {"description": "VALIDATION_FAILED"},
    401: {"description": "UNAUTHENTICATED"},
    403: {"description": "FORBIDDEN (the reason is in `detail`) or CSRF_FAILED"},
    404: {"description": "NOT_FOUND (missing, or not visible to you)"},
    409: {"description": "WORKFLOW_VIOLATION, ALREADY_CLAIMED or APPROVAL_ALREADY_PENDING"},
}
_VERSIONED: dict[int | str, dict[str, object]] = {
    **_PROBLEMS,
    412: {"description": "VERSION_CONFLICT, with `current` and `changes_since`"},
    422: {
        "description": "REASON_REQUIRED, APPROVAL_REQUIRED or IDEMPOTENCY_KEY_REUSED",
    },
    428: {"description": "PRECONDITION_REQUIRED: send If-Match"},
}

ItemKey = Annotated[str, Path(description="Item key, like PAY-142 (not case sensitive)")]
Run = Callable[[CommandTx], Awaitable[commands.Result]]


def _item(result: commands.Result, status: int = 200) -> Outcome:
    return Outcome(status, ItemOut.from_view(result.view).model_dump(mode="json"))


async def _run(
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: str | None,
    command: Run,
    *,
    status: int = 200,
) -> Response:
    """Run `command` once per (user, Idempotency-Key) and turn the service's two signals (a
    stale version, a lost claim) into the problem bodies SPEC 12 defines."""

    async def work(tx: CommandTx) -> Outcome:
        try:
            return _item(await command(tx), status)
        except items_service.StaleVersion as stale:
            raise VersionConflict(
                "This item changed since you opened it.",
                current=ItemOut.from_view(stale.current).model_dump(mode="json"),
                changes_since=[
                    EventOut.from_record(e).model_dump(mode="json") for e in stale.changes_since
                ],
            ) from stale
        except commands.ClaimLost as lost:
            owner = lost.view.record
            raise AlreadyClaimed(
                f"{owner.assignee_name or 'Someone'} already owns this item.",
                current=ItemOut.from_view(lost.view).model_dump(mode="json"),
                extras={
                    "claimed_by": {
                        "id": str(owner.assignee_id) if owner.assignee_id else None,
                        "name": owner.assignee_name,
                    },
                    "claimed_at": lost.claimed_at.isoformat() if lost.claimed_at else None,
                },
            ) from lost

    outcome = await run_idempotent(
        conn,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
        key=idempotency_key,
        request_fingerprint=await fingerprint(request),
        work=work,
    )
    return respond(outcome)


# ----- ownership ------------------------------------------------------------------------------


@router.post(
    "/{key}/claim",
    response_model=ItemOut,
    operation_id="claim_item",
    summary="Take ownership",
    responses=_PROBLEMS,
)
async def claim_item(
    key: ItemKey,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """Become the owner of an unassigned `new` item. When two people try at once exactly one wins;
    the other gets 409 `ALREADY_CLAIMED` with the owner and `claimed_at`. Claiming an item that is
    already yours is a 200 and changes nothing."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.claim(tx, actor.ctx, key.upper()),
    )


@router.post(
    "/{key}/release",
    response_model=ItemOut,
    operation_id="release_item",
    summary="Give up ownership",
    responses=_PROBLEMS,
)
async def release_item(
    key: ItemKey,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """The owner hands the item back: it returns to `new`, unassigned. Not while an approval is
    waiting; cancel it first."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.release(tx, actor.ctx, key.upper()),
    )


@router.post(
    "/{key}/assign",
    response_model=ItemOut,
    operation_id="assign_item",
    summary="Set or clear the owner",
    responses=_VERSIONED,
)
async def assign_item(
    key: ItemKey,
    body: AssignRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    expected_version: ExpectedVersion,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """A lead sets the owner to a member or lead of the team, or clears it with
    `assignee_id: null`. Needs `If-Match`."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.assign(
            tx, actor.ctx, key.upper(), expected_version, body.assignee_id, body.reason
        ),
    )


# ----- transitions ----------------------------------------------------------------------------


@router.post(
    "/{key}/transition",
    response_model=ItemOut,
    operation_id="transition_item",
    summary="Block, unblock, resolve, reopen, close or withdraw",
    responses=_VERSIONED,
)
async def transition_item(
    key: ItemKey,
    body: TransitionRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    expected_version: ExpectedVersion,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """Move the item along the workflow (SPEC 4.1). `reason` is required where the table says so
    (for `resolve` it is the resolution note). Resolving an item that requires approval without
    a valid approval is 422 `APPROVAL_REQUIRED`. Needs `If-Match`."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.transition(
            tx,
            actor.ctx,
            key.upper(),
            expected_version,
            Action(body.action),
            reason=body.reason,
            resolution=body.resolution,
            duplicate_of=body.duplicate_of,
        ),
    )


@router.post(
    "/{key}/transfer",
    response_model=ItemOut,
    operation_id="transfer_item",
    summary="Move to another team",
    responses=_VERSIONED,
)
async def transfer_item(
    key: ItemKey,
    body: TransferRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    expected_version: ExpectedVersion,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """A lead of the current team moves the item. The key does not change. An owner who is not in
    the new team is unassigned and the item goes back to `new`; a pending approval is cancelled
    and an approved one invalidated. The answer shows the item even if you can no longer see it."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.transfer(
            tx, actor.ctx, key.upper(), expected_version, body.team_key, body.reason
        ),
    )


# ----- approvals ------------------------------------------------------------------------------


@router.post(
    "/{key}/approvals",
    status_code=201,
    response_model=ItemOut,
    operation_id="request_item_approval",
    summary="Ask a lead to approve",
    responses=_VERSIONED,
)
async def request_item_approval(
    key: ItemKey,
    body: ApprovalRequestBody,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    expected_version: ExpectedVersion,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """The owner (or a lead) asks for approval of the item as it is now. Edits to the title,
    description or type afterwards invalidate it. A second request while one is waiting is 409
    `APPROVAL_ALREADY_PENDING`. Needs `If-Match`."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.request_approval(
            tx, actor.ctx, key.upper(), expected_version, body.note
        ),
        status=201,
    )


@router.post(
    "/{key}/approvals/{approval_id}/decision",
    response_model=ItemOut,
    operation_id="decide_item_approval",
    summary="Approve or reject",
    responses=_VERSIONED,
)
async def decide_item_approval(
    key: ItemKey,
    approval_id: uuid.UUID,
    body: DecisionRequest,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    expected_version: ExpectedVersion,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """A lead other than the requester decides. `If-Match` must be the version the approver
    reviewed: if the item changed since, the answer is 412 with what changed and nothing is
    approved. A rejection needs a `note`."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.decide(
            tx,
            actor.ctx,
            key.upper(),
            approval_id,
            expected_version,
            approve=body.approves,
            note=body.note,
        ),
    )


@router.post(
    "/{key}/approvals/{approval_id}/cancel",
    response_model=ItemOut,
    operation_id="cancel_item_approval",
    summary="Withdraw an approval request",
    responses=_PROBLEMS,
)
async def cancel_item_approval(
    key: ItemKey,
    approval_id: uuid.UUID,
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """The person who asked, or a lead, withdraws a waiting request. The item goes back to
    `in_progress`."""
    return await _run(
        request,
        actor,
        conn,
        idempotency_key,
        lambda tx: commands.cancel_approval(tx, actor.ctx, key.upper(), approval_id),
    )
