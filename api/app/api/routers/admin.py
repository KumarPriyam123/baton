"""Admin endpoints (SPEC 11): the outbox ("jobs") and its dead letters. Admins only (SPEC 5.2)."""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Request, Response
from pydantic import BaseModel

from app.api.constants import API_PREFIX
from app.api.deps import Conn, CurrentActor
from app.api.idempotency import OptionalIdempotencyKey, Outcome, fingerprint, run_idempotent
from app.api.pagination import ITEM_DEFAULT_LIMIT, ITEM_MAX_LIMIT
from app.api.responses import respond
from app.db.tx import CommandTx
from app.domain.enums import OutboxStatus
from app.domain.errors import Forbidden, NotFound, WorkflowViolation
from app.domain.policy import Action, can
from app.repo import outbox as outbox_repo

router = APIRouter(prefix=f"{API_PREFIX}/admin", tags=["admin"])


class JobOut(BaseModel):
    id: int
    topic: str
    status: OutboxStatus
    attempts: int
    available_at: datetime
    last_error: str | None
    created_at: datetime
    processed_at: datetime | None
    payload: dict[str, Any]

    @classmethod
    def from_record(cls, r: outbox_repo.JobRecord) -> "JobOut":
        return cls(**vars(r))


class JobsPage(BaseModel):
    items: list[JobOut]
    next_cursor: int | None


def _require_admin(actor: CurrentActor) -> None:
    decision = can(actor.ctx, Action.VIEW_JOBS)
    if not decision.allowed:
        raise Forbidden(decision.reason)


@router.get("/jobs", operation_id="list_jobs", summary="The outbox, newest first")
async def list_jobs(
    actor: CurrentActor,
    conn: Conn,
    status: Annotated[OutboxStatus | None, Query(description="pending | done | dead")] = None,
    cursor: Annotated[int | None, Query(ge=1, description="`next_cursor` of the last page")] = None,
    limit: Annotated[int, Query(ge=1, le=ITEM_MAX_LIMIT)] = ITEM_DEFAULT_LIMIT,
) -> JobsPage:
    """Jobs by status, with attempts and the last error. Dead ones are the dead letters."""
    _require_admin(actor)
    found, next_cursor = await outbox_repo.list_jobs(conn, status, cursor, limit)
    return JobsPage(items=[JobOut.from_record(j) for j in found], next_cursor=next_cursor)


@router.post(
    "/jobs/{job_id}/retry",
    response_model=JobOut,
    operation_id="retry_job",
    summary="Requeue a dead job",
)
async def retry_job(
    job_id: Annotated[int, Path(ge=1)],
    request: Request,
    actor: CurrentActor,
    conn: Conn,
    idempotency_key: OptionalIdempotencyKey,
) -> Response:
    """Dead -> pending with attempts 0, available now. A job that is not dead is 409."""
    _require_admin(actor)

    async def work(tx: CommandTx) -> Outcome:
        requeued = await outbox_repo.requeue_dead(tx.conn, job_id, tx.now)
        if requeued is None:
            raise NotFound("No such job.")
        if not requeued:
            raise WorkflowViolation("Only a dead job can be retried.")
        found, _ = await outbox_repo.list_jobs(tx.conn, None, job_id + 1, 1)
        return Outcome(200, JobOut.from_record(found[0]).model_dump(mode="json"))

    outcome = await run_idempotent(
        conn,
        actor_id=actor.user_id,
        request_id=getattr(request.state, "request_id", None),
        key=idempotency_key,
        request_fingerprint=await fingerprint(request),
        work=work,
    )
    return respond(outcome, with_etag=False)
