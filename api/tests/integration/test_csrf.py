"""SPEC 5.4: every unsafe method needs the CSRF cookie and a matching header.

The route list comes from the app's own OpenAPI document, so a route added in a later phase is
covered the day it exists, without anyone remembering to add a test.
"""

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app
from tests.integration.conftest import csrf_headers, sign_in
from tests.support.app import running_app

UNSAFE = ("post", "put", "patch", "delete")
EXEMPT = {("post", "/api/v1/auth/login")}  # no session yet; SPEC lists no other exception


async def unsafe_routes(client: httpx.AsyncClient) -> list[tuple[str, str]]:
    spec = (await client.get("/api/openapi.json")).json()
    return sorted(
        (method, path)
        for path, operations in spec["paths"].items()
        for method in operations
        if method in UNSAFE and (method, path) not in EXEMPT
    )


def concrete(path: str) -> str:
    out = path
    while "{" in out:
        start, end = out.index("{"), out.index("}")
        out = out[:start] + "00000000-0000-0000-0000-000000000000" + out[end + 1 :]
    return out


async def test_the_openapi_document_lists_unsafe_routes(api: httpx.AsyncClient) -> None:
    routes = await unsafe_routes(api)

    assert ("post", "/api/v1/auth/logout") in routes
    assert len(routes) >= 1


async def test_every_unsafe_route_without_the_header_is_403_csrf_failed(
    api: httpx.AsyncClient,
) -> None:
    await sign_in(api, "priya.lead@baton.test")  # signed in, cookies present, header absent
    routes = await unsafe_routes(api)

    for method, path in routes:
        response = await api.request(method, concrete(path), json={})

        assert response.status_code == 403, (method, path)
        body = response.json()
        assert body["code"] == "CSRF_FAILED", (method, path)
        assert body["request_id"] == response.headers["X-Request-ID"]


async def test_every_unsafe_route_with_a_wrong_header_is_403_csrf_failed(
    api: httpx.AsyncClient,
) -> None:
    await sign_in(api, "priya.lead@baton.test")
    routes = await unsafe_routes(api)

    for method, path in routes:
        response = await api.request(
            method, concrete(path), json={}, headers={"X-CSRF-Token": "forged"}
        )

        assert response.status_code == 403, (method, path)
        assert response.json()["code"] == "CSRF_FAILED", (method, path)


async def test_every_unsafe_route_without_any_session_is_403_not_401(
    api: httpx.AsyncClient,
) -> None:
    """CSRF is checked before authentication: a forged request learns nothing about sessions."""
    for method, path in await unsafe_routes(api):
        response = await api.request(method, concrete(path), json={})

        assert response.status_code == 403, (method, path)
        assert response.json()["code"] == "CSRF_FAILED", (method, path)


async def test_a_header_without_the_cookie_is_rejected(api: httpx.AsyncClient) -> None:
    await sign_in(api, "priya.lead@baton.test")
    header = csrf_headers(api)
    api.cookies.delete("baton_csrf")

    response = await api.post("/api/v1/auth/logout", headers=header)

    assert response.status_code == 403
    assert response.json()["code"] == "CSRF_FAILED"


async def test_a_matching_cookie_and_header_gets_through(api: httpx.AsyncClient) -> None:
    await sign_in(api, "priya.lead@baton.test")

    response = await api.post("/api/v1/auth/logout", headers=csrf_headers(api))

    assert response.status_code == 204


async def test_the_csrf_token_changes_on_every_login(api: httpx.AsyncClient) -> None:
    first = (await sign_in(api, "priya.lead@baton.test")).json()["csrf_token"]
    second = (await sign_in(api, "priya.lead@baton.test")).json()["csrf_token"]

    assert first != second


async def test_login_is_exempt_because_there_is_no_session_yet(api: httpx.AsyncClient) -> None:
    response = await sign_in(api, "priya.lead@baton.test")

    assert response.status_code == 200


async def test_safe_methods_never_need_the_token(api: httpx.AsyncClient) -> None:
    await sign_in(api, "priya.lead@baton.test")

    assert (await api.get("/api/v1/me")).status_code == 200
    assert (await api.get("/api/v1/healthz")).status_code == 200


@pytest.fixture
def app_with_new_unsafe_routes(app_settings: Settings) -> FastAPI:
    app = create_app(app_settings)

    @app.put("/api/v1/_new/thing")
    async def put_thing() -> dict[str, str]:
        return {"ok": "yes"}

    @app.patch("/api/v1/_new/thing")
    async def patch_thing() -> dict[str, str]:
        return {"ok": "yes"}

    @app.delete("/api/v1/_new/thing")
    async def delete_thing() -> dict[str, str]:
        return {"ok": "yes"}

    return app


async def test_a_route_added_later_is_protected_without_touching_the_middleware(
    app_with_new_unsafe_routes: FastAPI,
) -> None:
    transport = httpx.ASGITransport(app=app_with_new_unsafe_routes)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        for method in ("put", "patch", "delete"):
            response = await client.request(method, "/api/v1/_new/thing")

            assert response.status_code == 403, method
            assert response.json()["code"] == "CSRF_FAILED", method

        client.cookies.set("baton_csrf", "abc")
        ok = await client.put("/api/v1/_new/thing", headers={"X-CSRF-Token": "abc"})
        assert ok.status_code == 200


async def test_csrf_failure_runs_in_a_separate_app_with_the_lifespan(
    app_settings: Settings,
) -> None:
    async with running_app(app_settings) as client:
        response = await client.post("/api/v1/auth/logout")

    assert response.status_code == 403
