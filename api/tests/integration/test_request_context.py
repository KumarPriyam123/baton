import json
import re
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from app.config import Settings
from app.main import create_app


def _log_lines(captured: str) -> list[dict[str, Any]]:
    lines = []
    for raw in captured.splitlines():
        try:
            lines.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return lines


def _request_lines(captured: str) -> list[dict[str, Any]]:
    return [line for line in _log_lines(captured) if line.get("event") == "request"]


async def test_healthz_returns_ok(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_every_response_carries_a_generated_request_id(client: httpx.AsyncClient) -> None:
    first = await client.get("/api/v1/healthz")
    second = await client.get("/api/v1/healthz")

    assert re.fullmatch(r"[0-9a-f]{32}", first.headers["X-Request-ID"])
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


async def test_not_found_response_also_carries_a_request_id(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.headers["X-Request-ID"]


async def test_client_supplied_request_id_is_echoed(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/healthz", headers={"X-Request-ID": "trace-abc.123"})

    assert response.headers["X-Request-ID"] == "trace-abc.123"


@pytest.mark.parametrize("bad", ["has space", "a" * 129, "inject\\nline", "semi;colon"])
async def test_unsafe_request_id_is_replaced_not_echoed(
    client: httpx.AsyncClient, bad: str
) -> None:
    response = await client.get("/api/v1/healthz", headers={"X-Request-ID": bad})

    assert response.headers["X-Request-ID"] != bad
    assert re.fullmatch(r"[0-9a-f]{32}", response.headers["X-Request-ID"])


async def test_same_request_id_appears_in_the_api_log_line(
    capsys: pytest.CaptureFixture[str], settings: Settings
) -> None:
    app = create_app(settings)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/healthz")

    request_id = response.headers["X-Request-ID"]
    lines = _request_lines(capsys.readouterr().out)

    assert len(lines) == 1
    line = lines[0]
    assert line["request_id"] == request_id
    assert line["method"] == "GET"
    assert line["route"] == "/api/v1/healthz"
    assert line["status"] == 200
    assert isinstance(line["latency_ms"], float)
    assert "user_id" in line


async def test_unhandled_exception_returns_problem_json_with_request_id(
    capsys: pytest.CaptureFixture[str], settings: Settings
) -> None:
    app: FastAPI = create_app(settings)

    @app.get("/api/v1/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/v1/boom")

    request_id = response.headers["X-Request-ID"]
    body = response.json()
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/problem+json"
    assert body["code"] == "INTERNAL"
    assert body["request_id"] == request_id
    assert "kaboom" not in response.text  # never leak internals

    lines = _log_lines(capsys.readouterr().out)
    crash = next(line for line in lines if line.get("event") == "unhandled_exception")
    assert crash["request_id"] == request_id
    assert "kaboom" in crash["exception"]
    request_line = next(line for line in lines if line.get("event") == "request")
    assert request_line["status"] == 500
    assert request_line["request_id"] == request_id
