from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.api.constants import API_PREFIX
from app.db.migrations import check_ready

# Routers declare the full prefix themselves (not via include_router(prefix=...)) so the
# matched route template, and therefore the access log's `route` field, keeps /api/v1.
router = APIRouter(prefix=API_PREFIX, tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness only: the process is up. No database access."""
    return {"status": "ok"}


class ReadyOut(BaseModel):
    status: str
    database: bool
    migrations: bool
    current: str | None
    expected: str


@router.get("/readyz", response_model=ReadyOut, responses={503: {"model": ReadyOut}})
async def readyz(request: Request) -> JSONResponse:
    """Readiness: the database answers and its migrations are at the head this code expects."""
    report = await check_ready(request.app.state.engine)
    return JSONResponse(report.body(), status_code=200 if report.ready else 503)
