"""Request and response models for the item endpoints (SPEC 11)."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import (
    ApprovalStatus,
    DueSource,
    EventKind,
    ItemStatus,
    ItemType,
    Resolution,
)
from app.domain.policy import Action
from app.repo.events import EventRecord
from app.services.items import ItemView

TITLE_MIN, TITLE_MAX = 3, 200
DESCRIPTION_MAX = 20_000


def _no_nul(value: str) -> str:
    """Text Postgres can store. It rejects NUL, and a lone surrogate (valid in JSON, not in UTF-8)
    cannot even be encoded; a clear 400 beats a 500."""
    if "\x00" in value:
        raise ValueError("must not contain the NUL character")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as error:
        raise ValueError("must be valid Unicode text") from error
    return value


def _title(value: str) -> str:
    value = _no_nul(value).strip()
    if not TITLE_MIN <= len(value) <= TITLE_MAX:
        raise ValueError(f"must be {TITLE_MIN} to {TITLE_MAX} characters")
    return value


def _description(value: str) -> str:
    value = _no_nul(value)
    if len(value) > DESCRIPTION_MAX:
        raise ValueError(f"can be at most {DESCRIPTION_MAX:,} characters")
    return value


# ----- responses ----------------------------------------------------------------------------


class PersonOut(BaseModel):
    id: uuid.UUID
    name: str


class TeamRefOut(BaseModel):
    key: str
    name: str


class ApprovalOut(BaseModel):
    id: uuid.UUID
    status: ApprovalStatus
    requested_by: PersonOut
    requested_at: datetime
    decided_by: PersonOut | None
    decided_at: datetime | None


class NextStepOut(BaseModel):
    kind: str
    label: str
    severity: Literal["high", "medium", "low"]


class LastEventOut(BaseModel):
    kind: EventKind
    actor: PersonOut | None  # None: written by the system
    at: datetime


class ItemOut(BaseModel):
    id: uuid.UUID
    key: str
    version: int
    last_event_id: int | None
    team: TeamRefOut
    type: ItemType
    title: str
    description: str
    status: ItemStatus
    priority: int
    confidential: bool
    requires_approval: bool
    requester: PersonOut
    assignee: PersonOut | None
    resolution: Resolution | None
    resolution_note: str | None
    duplicate_of: str | None
    due_at: datetime | None
    due_source: DueSource
    sla_breached: bool
    approval: ApprovalOut | None
    next_step: NextStepOut
    allowed_actions: list[Action]
    last_event: LastEventOut | None
    unread_since_event_id: int | None
    watching: bool
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    closed_at: datetime | None

    @classmethod
    def from_view(cls, view: ItemView) -> Self:
        r = view.record
        approval = None
        if r.approval_id is not None and r.approval_status is not None:
            assert r.approval_requested_by_id is not None
            assert r.approval_requested_at is not None
            approval = ApprovalOut(
                id=r.approval_id,
                status=r.approval_status,
                requested_by=PersonOut(
                    id=r.approval_requested_by_id, name=r.approval_requested_by_name or ""
                ),
                requested_at=r.approval_requested_at,
                decided_by=(
                    PersonOut(id=r.approval_decided_by_id, name=r.approval_decided_by_name or "")
                    if r.approval_decided_by_id
                    else None
                ),
                decided_at=r.approval_decided_at,
            )
        last_event = None
        if r.last_event_kind is not None and r.last_event_at is not None:
            last_event = LastEventOut(
                kind=EventKind(r.last_event_kind),
                actor=(
                    PersonOut(id=r.last_event_actor_id, name=r.last_event_actor_name or "")
                    if r.last_event_actor_id
                    else None
                ),
                at=r.last_event_at,
            )
        return cls(
            id=r.id,
            key=r.key,
            version=r.version,
            last_event_id=r.last_event_id,
            team=TeamRefOut(key=r.team_key, name=r.team_name),
            type=r.type,
            title=r.title,
            description=r.description,
            status=r.status,
            priority=r.priority,
            confidential=r.confidential,
            requires_approval=r.requires_approval,
            requester=PersonOut(id=r.requester_id, name=r.requester_name),
            assignee=(
                PersonOut(id=r.assignee_id, name=r.assignee_name or "") if r.assignee_id else None
            ),
            resolution=r.resolution,
            resolution_note=r.resolution_note,
            duplicate_of=r.duplicate_of,
            due_at=r.due_at,
            due_source=r.due_source,
            sla_breached=r.sla_breached_at is not None,
            approval=approval,
            next_step=NextStepOut(
                kind=view.next_step.kind,
                label=view.next_step.label,
                severity=view.next_step.severity,
            ),
            allowed_actions=view.allowed_actions,
            last_event=last_event,
            unread_since_event_id=r.unread_since_event_id,
            watching=r.watching,
            created_at=r.created_at,
            updated_at=r.updated_at,
            resolved_at=r.resolved_at,
            closed_at=r.closed_at,
        )


class ItemsPage(BaseModel):
    items: list[ItemOut]
    next_cursor: str | None


class FacetsOut(BaseModel):
    """Counts of the items matching the same filters, by value. Keys are the enum values (priority
    as "0".."3", team as its key). A value with no items is absent."""

    status: dict[str, int]
    priority: dict[str, int]
    type: dict[str, int]
    team: dict[str, int]


class EventOut(BaseModel):
    id: int
    kind: EventKind
    actor: PersonOut | None
    item_version: int
    data: dict[str, Any]
    reason: str | None
    is_decision: bool
    created_at: datetime
    comment_body: str | None = Field(
        None, description="The comment's text, for `commented` events."
    )

    @classmethod
    def from_record(cls, e: EventRecord) -> Self:
        return cls(
            id=e.id,
            kind=e.kind,
            actor=PersonOut(id=e.actor_id, name=e.actor_name or "") if e.actor_id else None,
            item_version=e.item_version,
            data=e.data,
            reason=e.reason,
            is_decision=e.is_decision,
            created_at=e.created_at,
            comment_body=e.comment_body,
        )


class EventsPage(BaseModel):
    items: list[EventOut]
    next_cursor: int | None = Field(
        description="Event id to pass as `after_event_id` for the next page, or null at the end."
    )


# ----- requests -----------------------------------------------------------------------------

Priority = Annotated[int, Field(ge=0, le=3, description="0 = P0 (urgent) … 3 = P3 (low)")]


class CreateItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_key: Annotated[str, Field(pattern=r"^[A-Za-z]{2,5}$")]
    type: ItemType
    title: str
    description: str = ""
    priority: Priority = 2
    requires_approval: bool = Field(
        False,
        description="Ask for approval before this can be resolved. Only ops tasks may; "
        "other types take their default (SPEC 3.1).",
    )

    _title = field_validator("title")(_title)
    _description = field_validator("description")(_description)


_NOT_NULL_FIELDS = (
    "title",
    "description",
    "type",
    "priority",
    "due_at",
    "confidential",
    "requires_approval",
)


class PatchItemRequest(BaseModel):
    """Send only the fields to change (SPEC 4.2). Needs `If-Match`."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    description: str | None = None
    type: ItemType | None = None
    priority: Priority | None = None
    due_at: AwareDatetime | None = None
    confidential: bool | None = None
    requires_approval: bool | None = None
    reason: str | None = Field(
        None, max_length=2000, description="Why. Required to lower P0/P1 or turn approval off."
    )
    apply_type_defaults: bool = Field(
        False,
        description="With `type`: also reset requires_approval and confidential to the "
        "new type's defaults.",
    )

    @field_validator("reason")
    @classmethod
    def _check_reason(cls, value: str | None) -> str | None:
        return None if value is None else _no_nul(value)

    @field_validator("title")
    @classmethod
    def _check_title(cls, value: str | None) -> str | None:
        return None if value is None else _title(value)

    @field_validator("description")
    @classmethod
    def _check_description(cls, value: str | None) -> str | None:
        return None if value is None else _description(value)

    @model_validator(mode="after")
    def _only_real_changes(self) -> Self:
        sent = self.model_fields_set
        for name in _NOT_NULL_FIELDS:
            if name in sent and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        if not sent.intersection(_NOT_NULL_FIELDS):
            raise ValueError("send at least one field to change")
        if self.apply_type_defaults and "type" not in sent:
            raise ValueError("apply_type_defaults needs type")
        return self

    def fields(self) -> dict[str, Any]:
        return {
            name: getattr(self, name) for name in _NOT_NULL_FIELDS if name in self.model_fields_set
        }
