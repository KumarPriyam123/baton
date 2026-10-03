from fastapi import APIRouter

from app.api.constants import API_PREFIX

# Routers declare the full prefix themselves (not via include_router(prefix=...)) so the
# matched route template, and therefore the access log's `route` field, keeps /api/v1.
router = APIRouter(prefix=API_PREFIX, tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Liveness only. Readiness (DB reachable, migrations at head) arrives with phase 1."""
    return {"status": "ok"}
