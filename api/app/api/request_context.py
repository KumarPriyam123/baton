"""Request-id propagation and the one-line-per-request access log.

Pure ASGI (not BaseHTTPMiddleware) so contextvars and streaming responses behave.
"""

import json
import re
import time
import uuid

import structlog
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
# Accept only safe ids from the client: they end up in logs and response headers.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

log = structlog.get_logger("baton.request")


def resolve_request_id(raw: str | None) -> str:
    if raw and _VALID_REQUEST_ID.fullmatch(raw):
        return raw
    return uuid.uuid4().hex


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        raw = headers.get(REQUEST_ID_HEADER.lower().encode())
        request_id = resolve_request_id(raw.decode("latin-1") if raw else None)
        scope.setdefault("state", {})["request_id"] = request_id

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        started = time.perf_counter()
        status = 500
        response_started = False

        async def send_with_id(message: Message) -> None:
            nonlocal status, response_started
            if message["type"] == "http.response.start":
                response_started = True
                status = message["status"]
                MutableHeaders(scope=message)[REQUEST_ID_HEADER] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            log.exception("unhandled_exception")
            if response_started:
                raise
            await _send_internal_error(send_with_id, request_id)
        finally:
            route = scope.get("route")
            log.info(
                "request",
                method=scope["method"],
                route=getattr(route, "path", scope["path"]),
                status=status,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                user_id=scope.get("state", {}).get("user_id"),
            )
            structlog.contextvars.clear_contextvars()


async def _send_internal_error(send: Send, request_id: str) -> None:
    """problem+json for crashes, so even a 500 carries the request id (SPEC 12)."""
    body = json.dumps(
        {
            "type": "about:blank",
            "title": "Internal Server Error",
            "status": 500,
            "detail": "Something went wrong. Nothing was half-saved.",
            "code": "INTERNAL",
            "request_id": request_id,
        }
    ).encode()
    await send(
        {
            "type": "http.response.start",
            "status": 500,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
