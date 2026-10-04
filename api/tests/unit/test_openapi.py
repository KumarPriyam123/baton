"""/api/docs: every endpoint has a stable operation id and typed request and response models.

The frontend generates its client from this document (openapi-typescript), so a route without an
operation id or a model would arrive there as an anonymous `any`.
"""

import re
from typing import Any

import pytest

from app.config import Settings
from app.main import create_app

# Streams and probes whose bodies are not a typed JSON model by design.
# The SSE stream is text/event-stream, not JSON.
NO_JSON_MODEL: set[tuple[str, str]] = {("get", "/api/v1/stream")}


@pytest.fixture(scope="module")
def spec() -> dict[str, Any]:
    app = create_app(Settings(database_url="postgresql+asyncpg://x:x@localhost/x", env="test"))
    document: dict[str, Any] = app.openapi()
    return document


def operations(spec: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    return [
        (method, path, op)
        for path, methods in spec["paths"].items()
        for method, op in methods.items()
        if method in {"get", "post", "put", "patch", "delete"}
    ]


def test_every_operation_has_a_unique_readable_operation_id(spec: dict[str, Any]) -> None:
    ids: list[str] = [op.get("operationId", "") for _, _, op in operations(spec)]

    assert all(ids), "an operation has no operationId"
    assert len(ids) == len(set(ids)), "operation ids repeat"
    for operation_id in ids:
        # What FastAPI makes by default ends in the path and the method, like
        # `list_items_api_v1_items_get`; that changes whenever a route moves.
        assert re.fullmatch(r"[a-z][a-z0-9_]*", operation_id), operation_id
        assert not operation_id.endswith(("_get", "_post", "_patch", "_put", "_delete")), (
            operation_id
        )


def test_every_json_response_has_a_schema(spec: dict[str, Any]) -> None:
    missing = []
    for method, path, op in operations(spec):
        if (method, path) in NO_JSON_MODEL:
            continue
        for status, response in op["responses"].items():
            if not status.startswith("2") or status == "204":
                continue
            schema = response.get("content", {}).get("application/json", {}).get("schema")
            if not schema or schema == {}:
                missing.append(f"{method.upper()} {path} -> {status}")
    assert missing == [], f"untyped responses: {missing}"


def test_every_request_body_has_a_schema(spec: dict[str, Any]) -> None:
    missing = [
        f"{method.upper()} {path}"
        for method, path, op in operations(spec)
        if "requestBody" in op
        and not op["requestBody"]["content"]["application/json"].get("schema")
    ]
    assert missing == []


def test_the_item_endpoints_are_documented(spec: dict[str, Any]) -> None:
    ids = {op["operationId"] for _, _, op in operations(spec)}

    assert {
        "create_item",
        "list_items",
        "get_item_facets",
        "get_item",
        "update_item",
        "list_item_events",
    } <= ids


def test_the_edit_documents_its_preconditions_and_errors(spec: dict[str, Any]) -> None:
    patch = spec["paths"]["/api/v1/items/{key}"]["patch"]
    headers = {p["name"] for p in patch["parameters"] if p["in"] == "header"}

    assert {"If-Match", "Idempotency-Key"} <= headers
    assert {"412", "428", "409"} <= set(patch["responses"])
    create = spec["paths"]["/api/v1/items"]["post"]
    required = {p["name"] for p in create["parameters"] if p.get("required")}
    assert "Idempotency-Key" in required


def test_the_item_model_exposes_the_fields_the_ui_reads(spec: dict[str, Any]) -> None:
    item = spec["components"]["schemas"]["ItemOut"]["properties"]

    for name in (
        "version",
        "last_event_id",
        "allowed_actions",
        "next_step",
        "approval",
        "unread_since_event_id",
    ):
        assert name in item, name
