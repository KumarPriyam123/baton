"""`allowed_actions` and `next_step` for every demo persona on a sample of seeded items (phase 4
done-when). The expectations come from the hand-written SPEC table of T-FLOW, not from the code.
"""

import uuid
from typing import Any

import httpx

from app.config import Settings
from app.domain import workflow
from tests.integration.conftest import sign_in
from tests.support.items import ITEMS, rows
from tests.support.seeded import SeededDatabase
from tests.support.workflow import MEERA, many_clients
from tests.unit.test_workflow_flow import EXPECTED, LETTERS

PERSONAS = [
    "priya.lead",
    "vikram.lead",
    "asha",
    "rahul",
    "meera",
    "dev.viewer",
    "farah",
    "ishaan.lead",
    "sunita.lead",
    "admin",
]
WORKFLOW_ACTIONS = {a.value for a in workflow.TRANSITION_ACTIONS}
PER_STATUS = 3


async def sample_of_pay_items(seeded: SeededDatabase) -> list[dict[str, Any]]:
    """A few non-confidential Payments items in each status (the seed makes them all)."""
    found: list[dict[str, Any]] = []
    for status in ("new", "in_progress", "blocked", "awaiting_approval", "resolved", "closed"):
        found += await rows(
            seeded.url,
            "SELECT w.key, w.status::text AS status, w.requester_id, w.assignee_id, "
            "(SELECT a.requested_by FROM approvals a "
            "WHERE a.item_id = w.id AND a.status = 'pending') AS pending_by "
            "FROM work_items w JOIN teams t ON t.id = w.team_id "
            "WHERE t.key = 'PAY' AND NOT w.confidential AND w.status = $1::item_status "
            "ORDER BY w.key LIMIT $2",
            status,
            PER_STATUS,
        )
    return found


async def roles_of_pay(seeded: SeededDatabase) -> dict[uuid.UUID, tuple[str, bool]]:
    """user id -> (PAY role or '', is_admin) for each persona."""
    found = await rows(
        seeded.url,
        "SELECT u.id, u.is_admin, coalesce(m.role::text, '') AS role FROM users u "
        "LEFT JOIN memberships m ON m.user_id = u.id AND m.team_id = "
        "(SELECT id FROM teams WHERE key = 'PAY') WHERE u.email = ANY($1)",
        [f"{p}@baton.test" for p in PERSONAS],
    )
    return {r["id"]: (r["role"], r["is_admin"]) for r in found}


def relationships(user: uuid.UUID, role: str, is_admin: bool, item: dict[str, Any]) -> list[str]:
    """Every T-FLOW relationship this person has to this item. They stack: a lead can also be the
    requester, and then has the requester's rights too. Empty: they cannot see the item."""
    owns = item["assignee_id"] == user
    found: list[str] = []
    if role == "lead":
        found.append("lead_assignee" if owns else "lead")
    elif role == "member":
        found.append("assignee" if owns else "member")
    elif role == "viewer":
        found.append("viewer")
    if user == item["requester_id"]:
        found.append("requester")
    if is_admin:
        found.append("admin")
    return found


def expected_workflow_actions(
    rels: list[str], status: str, item: dict[str, Any], user: uuid.UUID
) -> set[str]:
    actions = {
        action.value
        for action, who in EXPECTED.items()
        for rel in rels
        if status in {LETTERS[c].value for c in who.get(rel, "")}
    }
    # The table assumes somebody else asked for the approval. The person who asked cannot decide
    # their own request but may withdraw it (SPEC 5.2).
    if status == "awaiting_approval" and item["pending_by"] == user:
        actions -= {"approve", "reject"}
        actions |= {"cancel_approval"}
    return actions


async def test_allowed_actions_for_each_demo_persona_match_the_spec_table(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    sample = await sample_of_pay_items(seeded)
    roles = await roles_of_pay(seeded)
    emails = [f"{p}@baton.test" for p in PERSONAS]
    ids = {
        r["email"]: r["id"]
        for r in await rows(
            seeded.url, "SELECT id, email::text AS email FROM users WHERE email = ANY($1)", emails
        )
    }
    assert len(sample) >= 12
    checked = 0

    async with many_clients_with_demo_password(app_settings, emails) as clients:
        for email, client in zip(emails, clients, strict=True):
            user = ids[email]
            role, is_admin = roles[user]
            for item in sample:
                rels = relationships(user, role, is_admin, item)
                response = await client.get(f"{ITEMS}/{item['key']}")
                if not rels:
                    assert response.status_code == 404, (email, item["key"])
                    continue
                assert response.status_code == 200, (email, item["key"], response.text)
                offered = set(response.json()["allowed_actions"]) & WORKFLOW_ACTIONS
                want = expected_workflow_actions(rels, item["status"], item, user)
                assert offered == want, (email, rels, item["key"], item["status"])
                checked += 1
    assert checked > 100


def many_clients_with_demo_password(settings: Settings, emails: list[str]) -> Any:
    from app.demo import DEMO_PASSWORD

    return many_clients(settings, emails, DEMO_PASSWORD)


async def test_next_step_reads_right_for_the_person_looking(
    seeded: SeededDatabase, app_settings: Settings
) -> None:
    waiting = await rows(
        seeded.url,
        "SELECT w.key, w.requester_id, a.requested_by FROM work_items w "
        "JOIN teams t ON t.id = w.team_id "
        "JOIN approvals a ON a.item_id = w.id AND a.status = 'pending' "
        "WHERE t.key = 'PAY' AND NOT w.confidential ORDER BY w.key LIMIT 1",
    )
    assert waiting, "the demo seed has an approval waiting in Payments"
    item = waiting[0]
    leads = await rows(
        seeded.url,
        "SELECT u.id, u.email::text AS email FROM users u JOIN memberships m ON m.user_id = u.id "
        "JOIN teams t ON t.id = m.team_id WHERE t.key = 'PAY' AND m.role = 'lead'",
    )
    other_lead = next(r for r in leads if r["id"] != item["requested_by"])
    from app.demo import DEMO_PASSWORD

    async with many_clients(
        app_settings, [other_lead["email"], "dev.viewer@baton.test"], DEMO_PASSWORD
    ) as (lead, viewer):
        by_lead = (await lead.get(f"{ITEMS}/{item['key']}")).json()["next_step"]
        by_viewer = (await viewer.get(f"{ITEMS}/{item['key']}")).json()["next_step"]

    assert by_lead == {"kind": "approval", "label": "Needs your approval", "severity": "high"}
    assert by_viewer["kind"] == "approval_wait"
    assert by_viewer["label"] == "Waiting for a Payments lead to approve"


async def test_a_new_unowned_item_asks_for_an_owner_whoever_looks(
    api: httpx.AsyncClient, seeded: SeededDatabase, app_settings: Settings
) -> None:
    from tests.support.items import created

    assert (await sign_in(api, MEERA)).status_code == 200
    item = await created(api, priority=0)

    assert item["next_step"]["kind"] == "needs_owner"
    assert item["next_step"]["severity"] == "high"  # P0
