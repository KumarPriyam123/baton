import uuid

from pydantic import BaseModel, ConfigDict

from app.domain.enums import TeamRole


class TeamOut(BaseModel):
    id: uuid.UUID
    key: str
    name: str
    description: str
    my_role: TeamRole | None  # the caller's role in this team, if any


class TeamPerson(BaseModel):
    id: uuid.UUID
    name: str
    email: str


class MemberOut(BaseModel):
    user: TeamPerson
    role: TeamRole


class MembersPage(BaseModel):
    items: list[MemberOut]
    next_cursor: str | None


class AddMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: uuid.UUID
    role: TeamRole


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: TeamRole


class DirectoryEntryOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str


class DirectoryPage(BaseModel):
    items: list[DirectoryEntryOut]
    next_cursor: str | None
