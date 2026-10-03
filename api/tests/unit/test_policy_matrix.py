"""SPEC 5.2, cell by cell. The expectations below are typed by hand from the SPEC; nothing here
is derived from the implementation, so a policy that drifts from the SPEC fails a test.

Personas (all relate to ONE item of team T, requested by R):
  V viewer of T            M member of T (not the assignee)     A member of T (the assignee)
  L, L2 leads of T         R requester, not in T                D admin, not in T
  S lead of ANOTHER team   AL admin who is also a lead of T
"""

import uuid
from dataclasses import dataclass

import pytest

from app.domain.enums import ItemStatus, TeamRole
from app.domain.policy import Action, ActorContext, Decision, ItemFacts, TeamFacts, can, can_view

T, OTHER = uuid.UUID(int=1), uuid.UUID(int=2)
PEOPLE = ("V", "M", "A", "L", "L2", "R", "D", "S", "AL")
ID = {name: uuid.UUID(int=100 + n) for n, name in enumerate(PEOPLE)}


PERSONAS: dict[str, ActorContext] = {
    "V": ActorContext(ID["V"], False, {T: TeamRole.VIEWER}),
    "M": ActorContext(ID["M"], False, {T: TeamRole.MEMBER}),
    "A": ActorContext(ID["A"], False, {T: TeamRole.MEMBER}),
    "L": ActorContext(ID["L"], False, {T: TeamRole.LEAD}),
    "L2": ActorContext(ID["L2"], False, {T: TeamRole.LEAD}),
    "R": ActorContext(ID["R"], False, {}),
    "D": ActorContext(ID["D"], True, {}),
    "S": ActorContext(ID["S"], False, {OTHER: TeamRole.LEAD}),
    "AL": ActorContext(ID["AL"], True, {T: TeamRole.LEAD}),
}
EVERYONE = frozenset(PERSONAS)


def s(text: str) -> frozenset[str]:
    return frozenset(text.split())


NOBODY = frozenset[str]()

# Every item action in the SPEC 5.2 matrix (plus SPEC 4.2's requires_approval rule), by name.
ITEM_ACTIONS = (
    "view",
    "comment",
    "watch",
    "edit_text",
    "change_priority",
    "edit_type",
    "edit_due_at",
    "edit_confidential",
    "requires_approval_on",
    "requires_approval_off",
    "claim",
    "release",
    "assign",
    "unassign",
    "block",
    "unblock",
    "resolve",
    "request_approval",
    "approve",
    "reject",
    "cancel_approval",
    "reopen",
    "close",
    "withdraw",
    "transfer",
)

# --- an open item, in progress, assigned to the member A -------------------------------------
IN_PROGRESS = {
    "view": s("V M A L L2 R D AL"),
    "comment": s("V M A L L2 R D AL"),  # viewers, members, leads, the requester and admins
    "watch": s("V M A L L2 R D AL"),
    "edit_text": s("A L L2 R AL"),  # member only if assignee; requester until resolved
    "change_priority": s("M A L L2 AL"),  # any member; requester only while new and unassigned
    "edit_type": s("L L2 AL"),
    "edit_due_at": s("L L2 AL"),
    "edit_confidential": s("L L2 AL"),
    "requires_approval_on": s("A L L2 AL"),  # assignee or lead
    "requires_approval_off": s("L L2 AL"),  # lead only
    "claim": s("M A L L2 AL"),  # members and leads
    "release": s("A"),  # only the assignee
    "assign": s("L L2 AL"),
    "unassign": s("L L2 AL"),
    "block": s("A L L2 AL"),
    "unblock": s("A L L2 AL"),
    "resolve": s("A L L2 AL"),
    "request_approval": s("A L L2 AL"),
    "approve": s("L L2 AL"),  # no approval is pending here, so no "own request" exclusion
    "reject": s("L L2 AL"),
    "cancel_approval": s("L L2 AL"),  # a member only if they requested it
    "reopen": s("A L L2 AL R"),  # resolved-style reopen: assignee, lead, requester
    "close": s("L L2 AL"),
    "withdraw": NOBODY,  # the requester only while the item is new
    "transfer": s("L L2 AL"),
}


@dataclass(frozen=True)
class Scenario:
    name: str
    item: ItemFacts
    expected: dict[str, frozenset[str]]
    visible: frozenset[str] = s("V M A L L2 R D AL")


def item(
    status: ItemStatus = ItemStatus.IN_PROGRESS,
    assignee: str | None = "A",
    confidential: bool = False,
    approval_by: str | None = None,
) -> ItemFacts:
    return ItemFacts(
        team_id=T,
        requester_id=ID["R"],
        assignee_id=ID[assignee] if assignee else None,
        confidential=confidential,
        status=status,
        team_name="Payments",
        pending_approval_requested_by=ID[approval_by] if approval_by else None,
    )


def only(base: dict[str, frozenset[str]], visible: frozenset[str]) -> dict[str, frozenset[str]]:
    """People who cannot see the item cannot do anything to it."""
    return {action: allowed & visible for action, allowed in base.items()}


NEW_UNASSIGNED = {
    **IN_PROGRESS,
    "edit_text": s("L L2 AL R"),  # A is a plain member now: not the assignee
    "change_priority": s("M A L L2 AL R"),  # the requester may, while new and unassigned
    "requires_approval_on": s("L L2 AL"),
    "release": NOBODY,
    "block": s("L L2 AL"),
    "unblock": s("L L2 AL"),
    "resolve": s("L L2 AL"),
    "request_approval": s("L L2 AL"),
    "reopen": s("L L2 AL R"),
    "withdraw": s("R"),  # "withdraw a new one"
}
RESOLVED = {
    **IN_PROGRESS,
    "edit_text": s("A L L2 AL"),  # the requester can no longer edit once it is resolved
    "reopen": s("A L L2 AL R"),
    "close": s("L L2 AL R"),  # the requester may close a resolved item
}
CLOSED = {
    **IN_PROGRESS,
    "edit_text": s("A L L2 AL"),
    "reopen": s("L L2 AL"),  # only a lead reopens a closed item
}
AWAITING_BY_MEMBER = {
    **IN_PROGRESS,
    "approve": s("L L2 AL"),
    "reject": s("L L2 AL"),
    "cancel_approval": s("M L L2 AL"),  # M asked, so M may cancel
}
AWAITING_BY_LEAD = {
    **IN_PROGRESS,
    "approve": s("L2 AL"),  # four-eyes: L asked, so L cannot decide
    "reject": s("L2 AL"),
    "cancel_approval": s("L L2 AL"),
}
AWAITING_BY_ADMIN_LEAD = {
    **IN_PROGRESS,
    "approve": s("L L2"),  # AL asked; being an admin does not change that
    "reject": s("L L2"),
    "cancel_approval": s("L L2 AL"),
}
ASSIGNED_TO_LEAD = {
    **IN_PROGRESS,
    "edit_text": s("L L2 AL R"),
    "requires_approval_on": s("L L2 AL"),
    "release": s("L"),  # only the assignee, and here that is the lead
    "block": s("L L2 AL"),  # member A is no longer the assignee
    "unblock": s("L L2 AL"),
    "resolve": s("L L2 AL"),
    "request_approval": s("L L2 AL"),
    "reopen": s("L L2 AL R"),
}

SCENARIOS = [
    Scenario("in progress, assigned to member A", item(), IN_PROGRESS),
    Scenario("new and unassigned", item(ItemStatus.NEW, None), NEW_UNASSIGNED),
    Scenario("resolved", item(ItemStatus.RESOLVED), RESOLVED),
    Scenario("closed", item(ItemStatus.CLOSED), CLOSED),
    Scenario(
        "awaiting approval requested by member M",
        item(ItemStatus.AWAITING_APPROVAL, approval_by="M"),
        AWAITING_BY_MEMBER,
    ),
    Scenario(
        "awaiting approval requested by lead L",
        item(ItemStatus.AWAITING_APPROVAL, approval_by="L"),
        AWAITING_BY_LEAD,
    ),
    Scenario(
        "awaiting approval requested by admin-lead AL",
        item(ItemStatus.AWAITING_APPROVAL, approval_by="AL"),
        AWAITING_BY_ADMIN_LEAD,
    ),
    Scenario("in progress, assigned to lead L", item(assignee="L"), ASSIGNED_TO_LEAD),
    # Confidential (SPEC A12): inside the team only leads and the assignee; the requester and
    # admins always. Viewers, other members and strangers do not even see it exists.
    Scenario(
        "confidential, assigned to member A",
        item(confidential=True),
        only(IN_PROGRESS, s("A L L2 R D AL")),
        visible=s("A L L2 R D AL"),
    ),
    Scenario(
        "confidential, assigned to lead L",
        item(assignee="L", confidential=True),
        only(ASSIGNED_TO_LEAD, s("L L2 R D AL")),
        visible=s("L L2 R D AL"),
    ),
    Scenario(
        "confidential, unassigned",
        item(ItemStatus.NEW, None, confidential=True),
        only(NEW_UNASSIGNED, s("L L2 R D AL")),
        visible=s("L L2 R D AL"),
    ),
]


def test_every_scenario_states_every_action_exactly_once() -> None:
    for scenario in SCENARIOS:
        assert sorted(scenario.expected) == sorted(ITEM_ACTIONS), scenario.name


def test_the_matrix_names_exactly_the_policy_item_actions() -> None:
    from app.domain.policy import ITEM_ACTIONS as POLICY_ITEM_ACTIONS

    assert {a.value for a in POLICY_ITEM_ACTIONS} == set(ITEM_ACTIONS)


def cells() -> list[tuple[str, str, str]]:
    return [
        (scenario.name, action, persona)
        for scenario in SCENARIOS
        for action in ITEM_ACTIONS
        for persona in PERSONAS
    ]


BY_NAME = {scenario.name: scenario for scenario in SCENARIOS}


@pytest.mark.parametrize(("scenario", "action", "persona"), cells())
def test_matrix_cell(scenario: str, action: str, persona: str) -> None:
    sc = BY_NAME[scenario]
    decision = can(PERSONAS[persona], Action(action), sc.item)

    if persona not in sc.visible:
        # Not visible: 404, never 403, so the item's existence does not leak.
        assert decision == Decision.not_found()
    elif persona in sc.expected[action]:
        assert decision == Decision.yes(), f"{persona} should be allowed to {action}"
    else:
        assert decision.allowed is False, f"{persona} should NOT be allowed to {action}"
        assert decision.code == "FORBIDDEN"
        assert decision.reason, "a 403 must say why"


@pytest.mark.parametrize("scenario", [sc.name for sc in SCENARIOS])
def test_can_view_agrees_with_the_view_action_and_the_expected_visibility(scenario: str) -> None:
    sc = BY_NAME[scenario]

    for persona, context in PERSONAS.items():
        assert can_view(context, sc.item) == (persona in sc.visible), persona


def test_a_stranger_sees_a_non_confidential_item_of_another_team_as_missing() -> None:
    decision = can(PERSONAS["S"], Action.COMMENT, item())

    assert decision.code == "NOT_FOUND"


# --- rows that are not about one item ------------------------------------------------------


def test_anyone_signed_in_can_raise_an_item_in_any_team() -> None:
    for context in PERSONAS.values():
        assert can(context, Action.RAISE_ITEM).allowed


@pytest.mark.parametrize("action", [Action.CREATE_TEAM, Action.VIEW_JOBS])
def test_only_admins_create_teams_and_view_jobs(action: Action) -> None:
    allowed = {name for name, context in PERSONAS.items() if can(context, action).allowed}

    assert allowed == s("D AL")


def test_leads_manage_members_and_viewers_of_their_own_team_and_admins_of_any() -> None:
    mine = {n for n, c in PERSONAS.items() if can(c, Action.MANAGE_MEMBERS, TeamFacts(T)).allowed}
    theirs = {
        n for n, c in PERSONAS.items() if can(c, Action.MANAGE_MEMBERS, TeamFacts(OTHER)).allowed
    }

    assert mine == s("L L2 D AL")
    assert theirs == s("S D AL")


def test_only_admins_manage_leads_even_leads_of_that_team() -> None:
    allowed = {n for n, c in PERSONAS.items() if can(c, Action.MANAGE_LEADS, TeamFacts(T)).allowed}

    assert allowed == s("D AL")
    refused = can(PERSONAS["L"], Action.MANAGE_LEADS, TeamFacts(T))
    assert refused.code == "FORBIDDEN"
    assert "admin" in (refused.reason or "").lower()


# --- rights add up: a person can be several things at once ----------------------------------


def test_requester_who_is_also_a_viewer_keeps_the_requester_rights() -> None:
    both = ActorContext(ID["R"], False, {T: TeamRole.VIEWER})

    assert can(both, Action.EDIT_TEXT, item()).allowed  # as requester, until resolved
    assert not can(both, Action.CLAIM, item()).allowed  # a viewer cannot claim
    assert not can(both, Action.EDIT_TEXT, item(ItemStatus.RESOLVED)).allowed


def test_requester_who_is_also_a_member_may_change_priority_after_assignment() -> None:
    both = ActorContext(ID["R"], False, {T: TeamRole.MEMBER})

    assert can(both, Action.CHANGE_PRIORITY, item()).allowed  # as a member


def test_admin_who_is_also_the_requester_still_cannot_edit_a_resolved_item() -> None:
    admin_requester = ActorContext(ID["R"], True, {})

    assert can(admin_requester, Action.EDIT_TEXT, item()).allowed
    assert not can(admin_requester, Action.EDIT_TEXT, item(ItemStatus.RESOLVED)).allowed


def test_an_admin_without_a_lead_role_cannot_approve() -> None:
    decision = can(PERSONAS["D"], Action.APPROVE, item(ItemStatus.AWAITING_APPROVAL))

    assert decision.allowed is False
    assert decision.code == "FORBIDDEN"
    assert "Payments" in (decision.reason or "")


def test_the_requester_of_an_approval_gets_a_specific_reason() -> None:
    facts = item(ItemStatus.AWAITING_APPROVAL, approval_by="L")

    decision = can(PERSONAS["L"], Action.APPROVE, facts)

    assert decision.reason == "You requested this approval, so someone else must decide it."


def test_policy_refuses_the_wrong_kind_of_facts() -> None:
    with pytest.raises(TypeError):
        can(PERSONAS["L"], Action.CLAIM, TeamFacts(T))
    with pytest.raises(TypeError):
        can(PERSONAS["L"], Action.MANAGE_MEMBERS, item())
    with pytest.raises(TypeError):
        can(PERSONAS["L"], Action.CLAIM)
