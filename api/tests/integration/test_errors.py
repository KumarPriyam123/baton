"""SPEC 12: every error is problem+json with a code from the table and the request id."""

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.config import Settings
from app.domain.errors import ALL_ERRORS, DomainError, RateLimited, ValidationFailed
from app.main import create_app

# Written by hand from the SPEC 12 table (code -> HTTP status). Not imported from the code.
SPEC_12 = {
    "VALIDATION_FAILED": 400,
    "UNAUTHENTICATED": 401,
    "FORBIDDEN": 403,
    "CSRF_FAILED": 403,
    "NOT_FOUND": 404,
    "METHOD_NOT_ALLOWED": 405,
    "ALREADY_CLAIMED": 409,
    "WORKFLOW_VIOLATION": 409,
    "APPROVAL_ALREADY_PENDING": 409,
    "VERSION_CONFLICT": 412,
    "APPROVAL_REQUIRED": 422,
    "REASON_REQUIRED": 422,
    "IDEMPOTENCY_KEY_REUSED": 422,
    "PRECONDITION_REQUIRED": 428,
    "RATE_LIMITED": 429,
    "BUSY": 503,
    "INTERNAL": 500,
}


class Payload(BaseModel):
    title: str = Field(min_length=3)
    priority: int = Field(ge=0, le=3)
    password: str


def build_app(settings: Settings) -> FastAPI:
    app = create_app(settings)

    @app.get("/api/v1/_raise/{code}")
    async def raise_error(code: str) -> None:
        error_class = next(c for c in ALL_ERRORS if c.code == code)
        raise error_class("Because of a test.", headers={"X-Test": "yes"})

    @app.post("/api/v1/_validate")
    async def validate(payload: Payload) -> dict[str, str]:
        return {"ok": payload.title}

    @app.get("/api/v1/_crash")
    async def crash() -> None:
        raise RuntimeError("secret internals: /srv/app/db.py line 42")

    return app


CSRF = {"X-CSRF-Token": "t"}


@pytest.fixture
async def errors_client(settings: Settings) -> httpx.AsyncClient:
    """Unsafe methods need the CSRF pair (SPEC 5.4), so this client carries one."""
    transport = httpx.ASGITransport(app=build_app(settings), raise_app_exceptions=False)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    client.cookies.set("baton_csrf", "t")
    return client


def test_error_classes_match_the_spec_table_exactly() -> None:
    assert {c.code: c.status for c in ALL_ERRORS} == SPEC_12
    assert len({c.code for c in ALL_ERRORS}) == len(ALL_ERRORS)


@pytest.mark.parametrize("code", sorted(SPEC_12))
async def test_every_code_becomes_problem_json_with_code_and_request_id(
    errors_client: httpx.AsyncClient, code: str
) -> None:
    response = await errors_client.get(f"/api/v1/_raise/{code}", headers={"X-Request-ID": "rid-1"})

    body = response.json()
    assert response.status_code == SPEC_12[code]
    assert response.headers["content-type"] == "application/problem+json"
    assert body["code"] == code
    assert body["status"] == SPEC_12[code]
    assert body["request_id"] == "rid-1" == response.headers["X-Request-ID"]
    assert body["detail"] == "Because of a test."
    assert body["type"].startswith("urn:baton:problem:")
    assert {"type", "title", "status", "detail", "code", "request_id"} <= set(body)
    assert response.headers["X-Test"] == "yes"  # headers such as Retry-After survive


async def test_validation_errors_are_400_with_field_errors_and_never_echo_input(
    errors_client: httpx.AsyncClient,
) -> None:
    response = await errors_client.post(
        "/api/v1/_validate", json={"title": "ab", "priority": 9, "password": 123}, headers=CSRF
    )

    body = response.json()
    assert response.status_code == 400
    assert body["code"] == "VALIDATION_FAILED"
    fields = {e["field"] for e in body["errors"]}
    assert {"title", "priority", "password"} <= fields
    assert all(set(e) == {"field", "message", "type"} for e in body["errors"])
    assert "123" not in response.text  # the submitted value must not come back


async def test_malformed_json_is_also_a_validation_failure(
    errors_client: httpx.AsyncClient,
) -> None:
    response = await errors_client.post(
        "/api/v1/_validate",
        content=b"{not json",
        headers={"content-type": "application/json", **CSRF},
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"


async def test_unknown_path_is_not_found_in_the_same_shape(
    errors_client: httpx.AsyncClient,
) -> None:
    response = await errors_client.get("/api/v1/nothing-here")

    body = response.json()
    assert response.status_code == 404
    assert body["code"] == "NOT_FOUND"
    assert body["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["content-type"] == "application/problem+json"


async def test_wrong_method_is_405_with_the_allow_header(errors_client: httpx.AsyncClient) -> None:
    response = await errors_client.delete("/api/v1/healthz", headers=CSRF)

    assert response.status_code == 405
    assert response.json()["code"] == "METHOD_NOT_ALLOWED"
    assert "GET" in response.headers["allow"]


async def test_unhandled_exceptions_are_500_internal_without_leaking_anything(
    errors_client: httpx.AsyncClient,
) -> None:
    response = await errors_client.get("/api/v1/_crash")

    body = response.json()
    assert response.status_code == 500
    assert body["code"] == "INTERNAL"
    assert body["request_id"] == response.headers["X-Request-ID"]
    for leaked in ("secret internals", "db.py", "Traceback", "RuntimeError"):
        assert leaked not in response.text


def test_rate_limited_and_validation_failed_carry_their_extras() -> None:
    limited = RateLimited("Try later.", headers={"Retry-After": "900"})
    invalid = ValidationFailed("Bad.", errors=[{"field": "x", "message": "m", "type": "t"}])

    assert limited.headers == {"Retry-After": "900"}
    assert invalid.errors == [{"field": "x", "message": "m", "type": "t"}]
    assert isinstance(limited, DomainError)
