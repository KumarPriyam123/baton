"""Teams, memberships and the user directory (SPEC 5.2, 11), through the real app."""

import uuid
from datetime import UTC, datetime

import httpx

from tests.integration.conftest import csrf_headers, sign_in
from tests.support.seeded import SeededDatabase
from tests.support.users import PASSWORD, create_user, fetch_one

PRIYA = "priya.lead@baton.test"  # lead of PAY
ASHA = "asha@baton.test"  # member of PAY; owns open PAY items in the seed
MEERA = "meera@baton.test"  # member of SUP, viewer on PAY
SUNITA = "sunita.lead@baton.test"  # lead of SUP
ADMIN = "admin@baton.test"


async def team_id(seeded: SeededDatabase, key: str) -> uuid.UUID:
    row = await fetch_one(seeded.url, "SELECT id FROM teams WHERE key = $1", key)
    assert row is not None
    found: uuid.UUID = row["id"]
    return found


async def new_user(seeded: SeededDatabase, **kwargs: object) -> tuple[str, uuid.UUID]:
    email = f"person-{uuid.uuid4().hex[:8]}@baton.test"
    user_id = await create_user(seeded.url, email, **kwargs)  # type: ignore[arg-type]
    return email, user_id


async def signed_in(client: httpx.AsyncClient, email: str) -> httpx.AsyncClient:
    response = await sign_in(client, email)
    assert response.status_code == 200, email
    return client


async def role_of(seeded: SeededDatabase, user_id: uuid.UUID, key: str) -> str | None:
    row = await fetch_one(
        seeded.url,
        "SELECT m.role::text AS role FROM memberships m JOIN teams t ON t.id = m.team_id "
        "WHERE m.user_id = $1 AND t.key = $2",
        user_id,
        key,
    )
    return None if row is None else str(row["role"])


# ----- reading ----------------------------------------------------------------------------


async def test_teams_and_members_need_a_session(api: httpx.AsyncClient) -> None:
    for path in ("/api/v1/teams", "/api/v1/teams/PAY/members", "/api/v1/users"):
        response = await api.get(path)

        assert response.status_code == 401, path
        assert response.json()["code"] == "UNAUTHENTICATED"


async def test_list_teams_shows_every_team_and_my_role_in_each(api: httpx.AsyncClient) -> None:
    await signed_in(api, PRIYA)

    teams = (await api.get("/api/v1/teams")).json()

    assert sorted(t["key"] for t in teams) == ["CMP", "ENG", "FIN", "PAY", "SRE", "SUP"]
    roles = {t["key"]: t["my_role"] for t in teams}
    assert roles == {"PAY": "lead", "CMP": None, "ENG": None, "FIN": None, "SRE": None, "SUP": None}
    assert all(t["name"] and t["description"] for t in teams)


async def test_members_are_paged_by_keyset_without_gaps_or_repeats(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, ASHA)
    everyone = (await api.get("/api/v1/teams/pay/members?limit=50")).json()
    assert everyone["next_cursor"] is None
    wanted = [m["user"]["id"] for m in everyone["items"]]
    assert len(wanted) >= 4

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(10):
        url = "/api/v1/teams/PAY/members?limit=2" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get(url)).json()
        assert len(page["items"]) <= 2
        seen += [m["user"]["id"] for m in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == wanted  # same order, nothing missing, nothing twice
    names = [m["user"]["name"] for m in everyone["items"]]
    assert names == sorted(names)


async def test_member_list_shows_roles_and_no_secrets(api: httpx.AsyncClient) -> None:
    await signed_in(api, ASHA)

    response = await api.get("/api/v1/teams/PAY/members?limit=50")

    roles = {m["user"]["email"]: m["role"] for m in response.json()["items"]}
    assert roles[PRIYA] == "lead"
    assert roles[ASHA] == "member"
    assert roles[MEERA] == "viewer"
    assert "password" not in response.text


async def test_unknown_team_and_bad_paging_input(api: httpx.AsyncClient) -> None:
    await signed_in(api, ASHA)

    assert (await api.get("/api/v1/teams/ZZZ/members")).status_code == 404
    assert (await api.get("/api/v1/teams/PAY/members?limit=0")).status_code == 400
    assert (await api.get("/api/v1/teams/PAY/members?limit=500")).status_code == 400
    bad = await api.get("/api/v1/teams/PAY/members?cursor=not-a-cursor")
    assert bad.status_code == 400
    assert bad.json()["code"] == "VALIDATION_FAILED"


# ----- who may change membership ----------------------------------------------------------


async def test_a_lead_adds_members_and_viewers_to_their_own_team(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, PRIYA)
    _, viewer = await new_user(seeded)
    _, member = await new_user(seeded)

    as_viewer = await api.post(
        "/api/v1/teams/PAY/members",
        json={"user_id": str(viewer), "role": "viewer"},
        headers=csrf_headers(api),
    )
    as_member = await api.post(
        "/api/v1/teams/PAY/members",
        json={"user_id": str(member), "role": "member"},
        headers=csrf_headers(api),
    )

    assert (as_viewer.status_code, as_member.status_code) == (201, 201)
    assert as_member.json()["role"] == "member"
    assert as_member.json()["user"]["id"] == str(member)
    assert await role_of(seeded, viewer, "PAY") == "viewer"
    assert await role_of(seeded, member, "PAY") == "member"


async def test_a_lead_cannot_add_a_lead_but_an_admin_can(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    _, user = await new_user(seeded)
    body = {"user_id": str(user), "role": "lead"}
    await signed_in(api, PRIYA)

    refused = await api.post("/api/v1/teams/PAY/members", json=body, headers=csrf_headers(api))

    assert refused.status_code == 403
    assert refused.json()["code"] == "FORBIDDEN"
    assert "admin" in refused.json()["detail"].lower()
    assert await role_of(seeded, user, "PAY") is None

    await signed_in(api, ADMIN)
    allowed = await api.post("/api/v1/teams/PAY/members", json=body, headers=csrf_headers(api))
    assert allowed.status_code == 201
    assert await role_of(seeded, user, "PAY") == "lead"


async def test_members_viewers_and_other_teams_leads_cannot_manage_membership(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    _, user = await new_user(seeded)
    body = {"user_id": str(user), "role": "member"}

    for who in (ASHA, MEERA, SUNITA):  # member, viewer, lead of ANOTHER team
        await signed_in(api, who)
        response = await api.post("/api/v1/teams/PAY/members", json=body, headers=csrf_headers(api))

        assert response.status_code == 403, who
        assert response.json()["code"] == "FORBIDDEN"
        assert "Payments" in response.json()["detail"]
    assert await role_of(seeded, user, "PAY") is None


async def test_an_admin_manages_any_team(api: httpx.AsyncClient, seeded: SeededDatabase) -> None:
    await signed_in(api, ADMIN)
    _, user = await new_user(seeded)

    response = await api.post(
        "/api/v1/teams/SRE/members",
        json={"user_id": str(user), "role": "member"},
        headers=csrf_headers(api),
    )

    assert response.status_code == 201


async def test_adding_the_same_role_twice_is_a_no_op_but_a_different_role_is_refused(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, PRIYA)
    _, user = await new_user(seeded)
    body = {"user_id": str(user), "role": "member"}
    first = await api.post("/api/v1/teams/PAY/members", json=body, headers=csrf_headers(api))

    again = await api.post("/api/v1/teams/PAY/members", json=body, headers=csrf_headers(api))
    other = await api.post(
        "/api/v1/teams/PAY/members",
        json={"user_id": str(user), "role": "viewer"},
        headers=csrf_headers(api),
    )

    assert (first.status_code, again.status_code) == (201, 200)
    assert other.status_code == 409
    assert other.json()["code"] == "WORKFLOW_VIOLATION"
    assert await role_of(seeded, user, "PAY") == "member"


async def test_adding_an_unknown_user_is_a_validation_error_on_that_field(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, PRIYA)

    response = await api.post(
        "/api/v1/teams/PAY/members",
        json={"user_id": str(uuid.uuid4()), "role": "member"},
        headers=csrf_headers(api),
    )

    assert response.status_code == 400
    assert response.json()["errors"][0]["field"] == "user_id"


async def test_a_deactivated_user_cannot_be_added(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, PRIYA)
    _, gone = await new_user(seeded, deactivated_at=datetime.now(UTC))

    response = await api.post(
        "/api/v1/teams/PAY/members",
        json={"user_id": str(gone), "role": "viewer"},
        headers=csrf_headers(api),
    )

    assert response.status_code == 400


async def test_a_bad_role_is_rejected(api: httpx.AsyncClient, seeded: SeededDatabase) -> None:
    await signed_in(api, PRIYA)
    _, user = await new_user(seeded)

    response = await api.post(
        "/api/v1/teams/PAY/members",
        json={"user_id": str(user), "role": "emperor"},
        headers=csrf_headers(api),
    )

    assert response.status_code == 400
    assert response.json()["code"] == "VALIDATION_FAILED"


async def test_a_lead_changes_viewers_and_members_but_only_an_admin_changes_leads(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    pay = await team_id(seeded, "PAY")
    _, viewer = await new_user(seeded, memberships={pay: "viewer"})
    _, member = await new_user(seeded, memberships={pay: "member"})
    _, lead = await new_user(seeded, memberships={pay: "lead"})
    await signed_in(api, PRIYA)
    headers = csrf_headers(api)

    up = await api.patch(
        f"/api/v1/teams/PAY/members/{viewer}", json={"role": "member"}, headers=headers
    )
    promote = await api.patch(
        f"/api/v1/teams/PAY/members/{member}", json={"role": "lead"}, headers=headers
    )
    demote = await api.patch(
        f"/api/v1/teams/PAY/members/{lead}", json={"role": "member"}, headers=headers
    )

    assert up.status_code == 200
    assert up.json()["role"] == "member"
    assert (promote.status_code, demote.status_code) == (403, 403)
    assert await role_of(seeded, member, "PAY") == "member"
    assert await role_of(seeded, lead, "PAY") == "lead"

    await signed_in(api, ADMIN)
    headers = csrf_headers(api)
    assert (
        await api.patch(
            f"/api/v1/teams/PAY/members/{member}", json={"role": "lead"}, headers=headers
        )
    ).status_code == 200
    assert (
        await api.patch(
            f"/api/v1/teams/PAY/members/{lead}", json={"role": "member"}, headers=headers
        )
    ).status_code == 200


async def test_changing_the_role_of_someone_not_in_the_team_is_404(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, PRIYA)
    _, stranger = await new_user(seeded)

    response = await api.patch(
        f"/api/v1/teams/PAY/members/{stranger}", json={"role": "viewer"}, headers=csrf_headers(api)
    )

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_a_lead_removes_a_viewer_and_it_takes_effect_on_their_very_next_request(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: object
) -> None:
    from app.config import Settings
    from tests.support.app import running_app

    assert isinstance(app_settings, Settings)
    pay = await team_id(seeded, "PAY")
    email, user = await new_user(seeded, memberships={pay: "member"})
    async with running_app(app_settings) as theirs:
        response = await sign_in(theirs, email, PASSWORD)
        assert [m["team"]["key"] for m in response.json()["memberships"]] == ["PAY"]
        before = {t["key"]: t["my_role"] for t in (await theirs.get("/api/v1/teams")).json()}
        assert before["PAY"] == "member"

        await signed_in(api, PRIYA)
        removal = await api.delete(f"/api/v1/teams/PAY/members/{user}", headers=csrf_headers(api))
        assert removal.status_code == 204

        # Their session is untouched, yet the very next request already reflects the change.
        me = await theirs.get("/api/v1/me")
        after = {t["key"]: t["my_role"] for t in (await theirs.get("/api/v1/teams")).json()}

    assert me.status_code == 200
    assert me.json()["memberships"] == []
    assert after["PAY"] is None


async def test_removing_a_lead_is_for_admins(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    pay = await team_id(seeded, "PAY")
    _, lead = await new_user(seeded, memberships={pay: "lead"})
    await signed_in(api, PRIYA)

    refused = await api.delete(f"/api/v1/teams/PAY/members/{lead}", headers=csrf_headers(api))
    assert refused.status_code == 403
    assert await role_of(seeded, lead, "PAY") == "lead"

    await signed_in(api, ADMIN)
    done = await api.delete(f"/api/v1/teams/PAY/members/{lead}", headers=csrf_headers(api))
    assert done.status_code == 204
    assert await role_of(seeded, lead, "PAY") is None


async def test_removing_someone_who_is_not_a_member_is_404(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, PRIYA)
    _, stranger = await new_user(seeded)

    response = await api.delete(f"/api/v1/teams/PAY/members/{stranger}", headers=csrf_headers(api))

    assert response.status_code == 404


# SPEC 4.3b (removing or demoting someone unassigns their open items) is in
# test_membership_unassigns.py.


async def test_demoting_a_lead_to_member_is_fine_even_if_they_own_items(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    """A member can still own items, so nothing is orphaned."""
    pay = await team_id(seeded, "PAY")
    _, lead = await new_user(seeded, memberships={pay: "lead"})
    await signed_in(api, ADMIN)

    response = await api.patch(
        f"/api/v1/teams/PAY/members/{lead}", json={"role": "member"}, headers=csrf_headers(api)
    )

    assert response.status_code == 200


async def test_membership_routes_without_a_session_are_401(api: httpx.AsyncClient) -> None:
    api.cookies.set("baton_csrf", "t")  # pass CSRF so the missing session is what fails
    headers = {"X-CSRF-Token": "t"}
    someone = uuid.uuid4()

    responses = [
        await api.post(
            "/api/v1/teams/PAY/members",
            json={"user_id": str(someone), "role": "viewer"},
            headers=headers,
        ),
        await api.patch(
            f"/api/v1/teams/PAY/members/{someone}", json={"role": "member"}, headers=headers
        ),
        await api.delete(f"/api/v1/teams/PAY/members/{someone}", headers=headers),
    ]

    assert [r.status_code for r in responses] == [401, 401, 401]


# ----- the directory ----------------------------------------------------------------------


async def test_directory_search_matches_name_or_email_case_insensitively(
    api: httpx.AsyncClient,
) -> None:
    await signed_in(api, ASHA)

    by_name = (await api.get("/api/v1/users?q=PRIYA")).json()["items"]
    by_email = (await api.get("/api/v1/users?q=dev.viewer")).json()["items"]

    assert [u["email"] for u in by_name] == [PRIYA]
    assert [u["email"] for u in by_email] == ["dev.viewer@baton.test"]
    assert set(by_name[0]) == {"id", "name", "email"}  # no hash, no flags


async def test_directory_treats_percent_and_underscore_literally(api: httpx.AsyncClient) -> None:
    await signed_in(api, ASHA)

    assert (await api.get("/api/v1/users?q=%25")).json()["items"] == []  # "%" is not a wildcard
    assert (await api.get("/api/v1/users?q=_")).json()["items"] == []


async def test_directory_pages_by_keyset_and_covers_everyone_once(api: httpx.AsyncClient) -> None:
    await signed_in(api, ASHA)
    everyone = (await api.get("/api/v1/users?limit=50")).json()
    wanted = [u["id"] for u in everyone["items"]]
    assert len(wanted) >= 24

    seen: list[str] = []
    cursor: str | None = None
    for _ in range(40):
        url = "/api/v1/users?limit=5" + (f"&cursor={cursor}" if cursor else "")
        page = (await api.get(url)).json()
        assert len(page["items"]) <= 5
        seen += [u["id"] for u in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break

    assert seen == wanted


async def test_directory_hides_deactivated_people(
    api: httpx.AsyncClient, seeded: SeededDatabase
) -> None:
    await signed_in(api, ASHA)
    email, _ = await new_user(seeded, name="Zed Departed", deactivated_at=datetime.now(UTC))

    items = (await api.get("/api/v1/users?q=Departed")).json()["items"]

    assert items == []
    assert email not in (await api.get("/api/v1/users?limit=50")).text
