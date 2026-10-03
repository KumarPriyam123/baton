"""Database errors seen from the domain: SQLSTATE and constraint name -> a SPEC 12 error.

Application code checks rules first and gives good messages. The database constraints (SPEC 3.2,
CLAUDE.md I10) are the last line of defence; when one fires anyway, the caller still gets a
problem+json answer with the right code instead of a 500. A constraint that is not listed here
is a bug, and stays a 500 on purpose.
"""

from collections.abc import Callable
from typing import Any

from sqlalchemy.exc import DBAPIError

from app.domain.errors import (
    ApprovalAlreadyPending,
    Busy,
    DomainError,
    Forbidden,
    ReasonRequired,
    ValidationFailed,
    WorkflowViolation,
)

LOCK_NOT_AVAILABLE = "55P03"  # lock_timeout fired
QUERY_CANCELED = "57014"  # statement_timeout fired
SERIALIZATION_FAILURE = "40001"
DEADLOCK_DETECTED = "40P01"
RETRYABLE = frozenset({SERIALIZATION_FAILURE, DEADLOCK_DETECTED})
TIMEOUTS = frozenset({LOCK_NOT_AVAILABLE, QUERY_CANCELED})

RETRY_AFTER_SECONDS = "1"


def _field(field: str, message: str) -> Callable[[], DomainError]:
    def build() -> DomainError:
        return ValidationFailed(
            message, errors=[{"field": field, "message": message, "type": "value_error"}]
        )

    return build


# constraint (or unique index) name -> the domain error to raise
CONSTRAINT_ERRORS: dict[str, Callable[[], DomainError]] = {
    "owner_when_active": lambda: WorkflowViolation("An item that is being worked needs an owner."),
    "resolution_when_done": lambda: WorkflowViolation(
        "A resolved or closed item needs a resolution."
    ),
    "duplicate_has_target": _field("duplicate_of", "A duplicate must name the item it duplicates."),
    "not_self_duplicate": _field("duplicate_of", "An item cannot be a duplicate of itself."),
    "work_items_title_length": _field("title", "The title must be 3 to 200 characters."),
    "work_items_description_length": _field(
        "description", "The description can be at most 20,000 characters."
    ),
    "work_items_priority_range": _field("priority", "The priority must be 0 to 3."),
    "approvals_one_pending": lambda: ApprovalAlreadyPending(
        "This item already has an approval request waiting for a decision."
    ),
    "approvals_four_eyes": lambda: Forbidden(
        "You requested this approval, so someone else must decide it."
    ),
    "approvals_rejection_has_note": lambda: ReasonRequired("A rejection needs a reason."),
    "comments_body_length": _field("body", "A comment must be 1 to 10,000 characters."),
    "memberships_pkey": lambda: WorkflowViolation("That person is already in the team."),
}


def _driver_error(error: DBAPIError) -> Any:
    """The asyncpg exception under SQLAlchemy's wrappers (it carries sqlstate and the name)."""
    orig = error.orig
    return getattr(orig, "__cause__", None) or orig


def sqlstate(error: DBAPIError) -> str | None:
    value = getattr(_driver_error(error), "sqlstate", None)
    return str(value) if value else None


def constraint_name(error: DBAPIError) -> str | None:
    value = getattr(_driver_error(error), "constraint_name", None)
    return str(value) if value else None


def translate(error: DBAPIError) -> DomainError | None:
    """The domain error for a database error, or None when it is not one we model.

    None for 40001 and 40P01 as well: those are for the retry loop in `run_command`, not for the
    client.
    """
    state = sqlstate(error)
    if state in TIMEOUTS:
        return Busy(
            "The system is busy. Try again in a moment.",
            headers={"Retry-After": RETRY_AFTER_SECONDS},
        )
    name = constraint_name(error)
    if name is not None and name in CONSTRAINT_ERRORS:
        return CONSTRAINT_ERRORS[name]()
    return None
