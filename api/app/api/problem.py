"""application/problem+json (RFC 9457) bodies, shared by handlers and middleware (SPEC 12)."""

import json
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import Send

PROBLEM_JSON = "application/problem+json"


def problem_body(
    *,
    status: int,
    code: str,
    title: str,
    detail: str,
    request_id: str | None,
    errors: list[dict[str, str]] | None = None,
    current: dict[str, Any] | None = None,
    changes_since: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "type": f"urn:baton:problem:{code.lower().replace('_', '-')}",
        "title": title,
        "status": status,
        "detail": detail,
        "code": code,
        "request_id": request_id,
    }
    if errors is not None:
        body["errors"] = errors
    if current is not None:
        body["current"] = current
    if changes_since is not None:
        body["changes_since"] = changes_since
    return body


def problem_response(body: dict[str, Any], headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(
        body, status_code=body["status"], media_type=PROBLEM_JSON, headers=headers or {}
    )


async def send_problem(
    send: Send, body: dict[str, Any], headers: dict[str, str] | None = None
) -> None:
    """For middleware, which has no Response object to return."""
    payload = json.dumps(body).encode()
    raw_headers = [
        (b"content-type", PROBLEM_JSON.encode()),
        (b"content-length", str(len(payload)).encode()),
    ]
    raw_headers += [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    await send({"type": "http.response.start", "status": body["status"], "headers": raw_headers})
    await send({"type": "http.response.body", "body": payload})
