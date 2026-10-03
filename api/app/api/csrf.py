"""CSRF double-submit protection (SPEC 5.4).

Every unsafe method must carry the `baton_csrf` cookie and an identical `X-CSRF-Token` header.
A page on another origin can make the browser send the cookie but cannot read it, so it cannot
produce the header. The rule is by HTTP method, not by route list, so a new endpoint is covered
the day it is added. Only login is exempt: there is no session to protect yet.

Runs before authentication on purpose: an unsafe request without the header is a 403
CSRF_FAILED whether or not the caller is signed in.
"""

import secrets

from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api.constants import API_PREFIX
from app.api.problem import problem_body, send_problem
from app.auth.sessions import CSRF_COOKIE, CSRF_HEADER

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
EXEMPT = frozenset({("POST", f"{API_PREFIX}/auth/login")})


def csrf_ok(cookie: str | None, header: str | None) -> bool:
    if not cookie or not header:
        return False
    return secrets.compare_digest(cookie.encode(), header.encode())


class CsrfMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] not in UNSAFE_METHODS:
            await self.app(scope, receive, send)
            return
        if (scope["method"], scope["path"]) in EXEMPT:
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        if csrf_ok(request.cookies.get(CSRF_COOKIE), request.headers.get(CSRF_HEADER)):
            await self.app(scope, receive, send)
            return

        body = problem_body(
            status=403,
            code="CSRF_FAILED",
            title="CSRF check failed",
            detail="Missing or wrong CSRF token. Reload the page and try again.",
            request_id=scope.get("state", {}).get("request_id"),
        )
        await send_problem(send, body)
