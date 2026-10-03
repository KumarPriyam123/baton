from fastapi import APIRouter

from app.api.constants import API_PREFIX
from app.api.deps import AppSettings, Conn
from app.api.schemas.auth import DemoMembership, DemoUser, DemoUsersOut
from app.demo import DEMO_PASSWORD
from app.domain.errors import NotFound
from app.repo import users as users_repo

router = APIRouter(prefix=f"{API_PREFIX}/demo", tags=["demo"])


@router.get("/users")
async def list_demo_users(settings: AppSettings, conn: Conn) -> DemoUsersOut:
    """The quick account switcher for the login page. Exists only when DEMO_MODE=true; in any
    other configuration it is a plain 404, as if the route were not there."""
    if not settings.demo_mode:
        raise NotFound("This resource doesn't exist or you no longer have access.")
    people = await users_repo.list_demo_users(conn)
    return DemoUsersOut(
        password=DEMO_PASSWORD,
        users=[
            DemoUser(
                email=p.email,
                name=p.name,
                is_admin=p.is_admin,
                memberships=[DemoMembership(team_key=k, role=r) for k, r in p.memberships],
            )
            for p in people
        ],
    )
