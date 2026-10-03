"""Request bodies of the workflow commands (SPEC 11). The answers are `ItemOut`."""

import uuid
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.api.schemas.items import _no_nul
from app.domain.enums import Resolution

Reason = Annotated[str | None, Field(max_length=2000)]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _text_is_storable(cls, value: object) -> object:
        return _no_nul(value) if isinstance(value, str) else value


class AssignRequest(_Body):
    assignee_id: uuid.UUID | None = Field(
        description="The new owner (a member or lead of the team), or null to unassign.",
    )
    reason: Reason = Field(None, description="Optional note for an unassign.")


TransitionAction = Literal["block", "unblock", "resolve", "reopen", "close", "withdraw"]


class TransitionRequest(_Body):
    action: TransitionAction
    reason: Reason = Field(
        None,
        description="Why. Required for block, resolve (the note), reopen, close of an open item "
        "and withdraw; optional when closing a resolved item.",
    )
    resolution: Resolution | None = Field(
        None, description="Required to close an open item; resolve defaults to `done`."
    )
    duplicate_of: Annotated[str | None, Field(pattern=r"^[A-Za-z]{2,5}-\d{1,9}$")] = Field(
        None, description="Key of the item this duplicates, with resolution `duplicate`."
    )


class TransferRequest(_Body):
    team_key: Annotated[str, Field(pattern=r"^[A-Za-z]{2,5}$")]
    reason: Reason = Field(None, description="Required.")


class ApprovalRequestBody(_Body):
    note: Reason = Field(None, description="What the approver should look at.")


class DecisionRequest(_Body):
    decision: Literal["approve", "reject"]
    note: Reason = Field(None, description="Required to reject; optional to approve.")

    @property
    def approves(self) -> bool:
        return self.decision == "approve"
