"""Enumerations from SPEC 3.1 and the event kinds from SPEC 3.2. Pure: no I/O.

tests/db/test_schema_drift.py checks these against the labels stored in Postgres.
"""

from enum import StrEnum


class TeamRole(StrEnum):
    VIEWER = "viewer"
    MEMBER = "member"
    LEAD = "lead"


class ItemType(StrEnum):
    INCIDENT = "incident"
    CUSTOMER_ISSUE = "customer_issue"
    PAYMENT_INVESTIGATION = "payment_investigation"
    ENGINEERING = "engineering"
    COMPLIANCE_REQUEST = "compliance_request"
    OPS_TASK = "ops_task"


class ItemStatus(StrEnum):
    NEW = "new"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    AWAITING_APPROVAL = "awaiting_approval"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Resolution(StrEnum):
    DONE = "done"
    DUPLICATE = "duplicate"
    WONT_DO = "wont_do"
    CANNOT_REPRODUCE = "cannot_reproduce"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


class DueSource(StrEnum):
    AUTO = "auto"
    MANUAL = "manual"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    DEAD = "dead"


class EventKind(StrEnum):
    CREATED = "created"
    FIELD_CHANGED = "field_changed"
    PRIORITY_CHANGED = "priority_changed"
    ASSIGNED = "assigned"
    UNASSIGNED = "unassigned"
    STATUS_CHANGED = "status_changed"
    BLOCKED = "blocked"
    UNBLOCKED = "unblocked"
    RESOLVED = "resolved"
    REOPENED = "reopened"
    CLOSED = "closed"
    TRANSFERRED = "transferred"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_APPROVED = "approval_approved"
    APPROVAL_REJECTED = "approval_rejected"
    APPROVAL_CANCELLED = "approval_cancelled"
    APPROVAL_INVALIDATED = "approval_invalidated"
    REQUIRES_APPROVAL_CHANGED = "requires_approval_changed"
    CONFIDENTIAL_CHANGED = "confidential_changed"
    COMMENTED = "commented"
    SLA_BREACHED = "sla_breached"
    DUPLICATE_SUGGESTED = "duplicate_suggested"
