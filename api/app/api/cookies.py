"""The two cookies (SPEC 5.4): the session token (HttpOnly) and the readable CSRF token."""

from fastapi import Request, Response

from app.auth.sessions import CSRF_COOKIE, SESSION_COOKIE


def _secure(request: Request) -> bool:
    # Behind nginx, uvicorn's --proxy-headers turns X-Forwarded-Proto into the URL scheme.
    return request.url.scheme == "https"


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    # No Max-Age: the cookie lasts as long as the browser session; the server enforces the
    # 12-hour idle expiry, so a stolen-but-idle cookie dies on the server regardless.
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        secure=_secure(request),
        path="/",
    )


def set_csrf_cookie(response: Response, request: Request, token: str) -> None:
    # Readable by the page's JavaScript on purpose: it is copied into the X-CSRF-Token header.
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=False,
        samesite="lax",
        secure=_secure(request),
        path="/",
    )


def clear_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
