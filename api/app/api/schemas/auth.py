import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.domain.enums import TeamRole


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    name: str
    is_admin: bool


class TeamRef(BaseModel):
    key: str
    name: str


class MembershipOut(BaseModel):
    team: TeamRef
    role: TeamRole


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    csrf_token: str


class DemoMembership(BaseModel):
    team_key: str
    role: TeamRole


class DemoUser(BaseModel):
    email: str
    name: str
    is_admin: bool
    memberships: list[DemoMembership]


class DemoUsersOut(BaseModel):
    password: str
    users: list[DemoUser]
