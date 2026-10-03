"""Request and response models for comments, notifications and attention (SPEC 7, 11)."""

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.api.schemas.items import ItemOut, PersonOut, _no_nul

COMMENT_MAX = 10_000


class CommentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(description=f"1 to {COMMENT_MAX:,} characters. Markdown, shown safely.")

    @field_validator("body")
    @classmethod
    def _check_body(cls, value: str) -> str:
        value = _no_nul(value).strip()
        if not 1 <= len(value) <= COMMENT_MAX:
            raise ValueError(f"must be 1 to {COMMENT_MAX:,} characters")
        return value


class CommentOut(BaseModel):
    id: int
    event_id: int
    author: PersonOut
    body: str
    created_at: datetime


class CommentCreated(BaseModel):
    """The comment, and the item as it is now (its `last_event_id` moved, its `version` did not)."""

    comment: CommentOut
    item: ItemOut


# ----- attention (SPEC 7) -----------------------------------------------------------------------

SectionKey = Literal["approvals", "urgent", "unowned", "quiet", "requests"]


class AttentionSection(BaseModel):
    key: SectionKey
    title: str
    count: int = Field(description="Capped at 100; show it as '100+' when it is 100.")
    items: list[ItemOut] = Field(description="The first 10, in the section's order.")


class AttentionOut(BaseModel):
    sections: list[AttentionSection]


# ----- notifications ----------------------------------------------------------------------------


class NotificationOut(BaseModel):
    id: int
    kind: str = Field(description="The kind of the event that caused it, like `commented`.")
    item_key: str
    item_title: str
    event_id: int
    actor: PersonOut | None
    created_at: datetime
    read_at: datetime | None


class NotificationsPage(BaseModel):
    items: list[NotificationOut]
    next_cursor: int | None = Field(description="Pass as `cursor` for the next page, or null.")


class MarkReadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[int] | None = Field(None, max_length=100, description="Notification ids to mark.")
    all: bool = Field(False, description="Mark every unread notification of yours.")

    @model_validator(mode="after")
    def _one_of(self) -> Self:
        if self.all == bool(self.ids):
            raise ValueError("send either `ids` or `all: true`")
        return self


class MarkedRead(BaseModel):
    marked: int


# ----- similar items ----------------------------------------------------------------------------


class SimilarItemOut(BaseModel):
    id: uuid.UUID
    key: str
    title: str
    status: str
    priority: int
    score: float
