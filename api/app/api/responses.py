"""Turning a command's `Outcome` into an HTTP response, shared by every item router."""

from fastapi.responses import JSONResponse

from app.api.constants import API_PREFIX
from app.api.idempotency import Outcome
from app.api.preconditions import etag
from app.api.problem import PROBLEM_JSON


def respond(outcome: Outcome, *, location: bool = False) -> JSONResponse:
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
