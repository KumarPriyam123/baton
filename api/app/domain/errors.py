"""Domain errors: one class per row of the SPEC 12 error table. Pure: no I/O, no HTTP.

Code raises one of these; app/api/errors.py turns it into application/problem+json. Status and
code live here, once, so an endpoint can never invent its own.
"""

from typing import Any


class DomainError(Exception):
    status: int = 500
    code: str = "INTERNAL"
    title: str = "Internal Server Error"

    def __init__(
        self,
        detail: str | None = None,
        *,
        headers: dict[str, str] | None = None,
        errors: list[dict[str, str]] | None = None,
        current: dict[str, Any] | None = None,
        changes_since: list[dict[str, Any]] | None = None,
        extras: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail or self.title)
        self.detail = detail or self.title
        self.headers = headers or {}
        self.errors = errors
        self.current = current
        self.changes_since = changes_since
        self.extras = extras  # extension members of the problem body, e.g. who claimed it


class ValidationFailed(DomainError):
    status = 400
    code = "VALIDATION_FAILED"
    title = "Validation failed"


class Unauthenticated(DomainError):
    status = 401
    code = "UNAUTHENTICATED"
    title = "Authentication required"


class Forbidden(DomainError):
    status = 403
    code = "FORBIDDEN"
    title = "Forbidden"


class CsrfFailed(DomainError):
    status = 403
    code = "CSRF_FAILED"
    title = "CSRF check failed"


class NotFound(DomainError):
    status = 404
    code = "NOT_FOUND"
    title = "Not found"


class MethodNotAllowed(DomainError):
    status = 405
    code = "METHOD_NOT_ALLOWED"
    title = "Method not allowed"


class AlreadyClaimed(DomainError):
    status = 409
    code = "ALREADY_CLAIMED"
    title = "Already claimed"


class WorkflowViolation(DomainError):
    status = 409
    code = "WORKFLOW_VIOLATION"
    title = "Not allowed from the current state"


class ApprovalAlreadyPending(DomainError):
    status = 409
    code = "APPROVAL_ALREADY_PENDING"
    title = "An approval is already pending"


class VersionConflict(DomainError):
    status = 412
    code = "VERSION_CONFLICT"
    title = "The item changed"


class ApprovalRequired(DomainError):
    status = 422
    code = "APPROVAL_REQUIRED"
    title = "Approval required"


class ReasonRequired(DomainError):
    status = 422
    code = "REASON_REQUIRED"
    title = "A reason is required"


class IdempotencyKeyReused(DomainError):
    status = 422
    code = "IDEMPOTENCY_KEY_REUSED"
    title = "Idempotency key reused for a different request"


class PreconditionRequired(DomainError):
    status = 428
    code = "PRECONDITION_REQUIRED"
    title = "If-Match is required"


class RateLimited(DomainError):
    status = 429
    code = "RATE_LIMITED"
    title = "Too many attempts"


class Busy(DomainError):
    status = 503
    code = "BUSY"
    title = "Busy, try again"


class Internal(DomainError):
    status = 500
    code = "INTERNAL"
    title = "Internal Server Error"


ALL_ERRORS: tuple[type[DomainError], ...] = (
    ValidationFailed,
    Unauthenticated,
    Forbidden,
    CsrfFailed,
    NotFound,
    MethodNotAllowed,
    AlreadyClaimed,
    WorkflowViolation,
    ApprovalAlreadyPending,
    VersionConflict,
    ApprovalRequired,
    ReasonRequired,
    IdempotencyKeyReused,
    PreconditionRequired,
    RateLimited,
    Busy,
    Internal,
)
