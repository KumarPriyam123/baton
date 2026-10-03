"""Turns exceptions into problem+json responses with `code` and `request_id` (SPEC 12)."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import JSONResponse

from app.api.problem import problem_body, problem_response
from app.domain.errors import DomainError, MethodNotAllowed, NotFound, ValidationFailed


def _request_id(request: Request) -> str | None:
    value = getattr(request.state, "request_id", None)
    return str(value) if value else None


async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, DomainError)
    body = problem_body(
        status=exc.status,
        code=exc.code,
        title=exc.title,
        detail=exc.detail,
        request_id=_request_id(request),
        errors=exc.errors,
        current=exc.current,
        changes_since=exc.changes_since,
        extras=exc.extras,
    )
    return problem_response(body, exc.headers)


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """FastAPI answers 422 by default; SPEC 12 says 400 VALIDATION_FAILED with field errors.

    The offending input is deliberately left out of the body: it may be a password.
    """
    assert isinstance(exc, RequestValidationError)
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"][1:]) or str(error["loc"][0]),
            "message": str(error["msg"]),
            "type": str(error["type"]),
        }
        for error in exc.errors()
    ]
    return await handle_domain_error(
        request, ValidationFailed("The request has invalid fields.", errors=errors)
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Router-level errors (unknown path, wrong method) in the same shape as everything else."""
    assert isinstance(exc, StarletteHTTPException)
    headers = dict(exc.headers or {})
    if exc.status_code == 404:
        error: DomainError = NotFound("This resource doesn't exist or you no longer have access.")
    elif exc.status_code == 405:
        error = MethodNotAllowed("This method is not allowed on this path.")
    else:
        error = ValidationFailed(str(exc.detail))
        error.status = exc.status_code  # keep the real status for the rare 4xx we do not model
    error.headers = headers
    return await handle_domain_error(request, error)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(DomainError, handle_domain_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
