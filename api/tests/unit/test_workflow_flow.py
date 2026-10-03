"""T-FLOW (SPEC 14, CB4): every action x every status x every relationship against a table written
by hand from SPEC 4.1 (what the state allows) and 5.2 (who). The table is deliberately not derived
from workflow.py or policy.py: if either drifts from the SPEC, a cell here goes red.

Also the rules of `evaluate` one at a time (reasons, resolutions, the approval gate), and what each
valid command plans to write.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from app.domain import workflow
from app.domain.enums import ApprovalStatus, EventKind, ItemStatus, Resolution, TeamRole
from app.domain.hashing import subject_hash
from app.domain.policy import Action, ActorContext, ItemFacts

TEAM = uuid.UUID(int=1)
NEW_TEAM = uuid.UUID(int=2)
ME = uuid.UUID(int=100)  # the person asking
SOMEONE = uuid.UUID(int=200)  # everybody else: the other requester, owner or approval requester
NOW = datetime(2025, 3, 1, 12, 0, tzinfo=UTC)

N, I, B, A, R, C = (  # noqa: E741
    ItemStatus.NEW,
    ItemStatus.IN_PROGRESS,
    ItemStatus.BLOCKED,
    ItemStatus.AWAITING_APPROVAL,
    ItemStatus.RESOLVED,
    ItemStatus.CLOSED,
)
LETTERS = {"N": N, "I": I, "B": B, "A": A, "R": R, "C": C}

RELATIONSHIPS = (
    "requester",  # raised it; not in the team
    "assignee",  # a member who owns it
    "member",  # a member who does not
    "lead",  # a lead who does not own it
    "lead_assignee",  # a lead who owns it
    "viewer",
    "admin",  # not in the team
    "outsider",  # nothing to do with it
)

# SPEC 4.1 x 5.2, by hand. action -> relationship -> the statuses (letters) where it is allowed.
# A relationship that is missing may never do the action. An owner exists in every status but NEW.
EXPECTED: dict[Action, dict[str, str]] = {
    Action.CLAIM: {"assignee": "N", "member": "N", "lead": "N", "lead_assignee": "N"},
    Action.RELEASE: {"assignee": "IB", "lead_assignee": "IB"},
    Action.ASSIGN: {"lead": "NIB", "lead_assignee": "NIB"},  # not while awaiting approval
    Action.UNASSIGN: {"lead": "IB", "lead_assignee": "IB"},
    Action.BLOCK: {"assignee": "I", "lead": "I", "lead_assignee": "I"},
    Action.UNBLOCK: {"assignee": "B", "lead": "B", "lead_assignee": "B"},
    Action.REQUEST_APPROVAL: {"assignee": "I", "lead": "I", "lead_assignee": "I"},
    Action.APPROVE: {"lead": "A", "lead_assignee": "A"},
    Action.REJECT: {"lead": "A", "lead_assignee": "A"},
    Action.CANCEL_APPROVAL: {"lead": "A", "lead_assignee": "A"},
    Action.RESOLVE: {"assignee": "I", "lead": "I", "lead_assignee": "I"},
    Action.REOPEN: {"requester": "R", "assignee": "R", "lead": "RC", "lead_assignee": "RC"},
    Action.CLOSE: {"lead": "NIBR", "lead_assignee": "NIBR", "requester": "R"},
    Action.WITHDRAW: {"requester": "N"},
    Action.TRANSFER: {"lead": "NIBA", "lead_assignee": "NIBA"},
}


def persona(
    relationship: str, status: ItemStatus
) -> tuple[ActorContext, ItemFacts, workflow.WorkflowFacts]:
    roles: dict[uuid.UUID, TeamRole] = {}
    requester, owns = SOMEONE, False
    match relationship:
        case "requester":
            requester = ME
        case "assignee":
            roles, owns = {TEAM: TeamRole.MEMBER}, True
        case "member":
            roles = {TEAM: TeamRole.MEMBER}
        case "lead":
            roles = {TEAM: TeamRole.LEAD}
        case "lead_assignee":
            roles, owns = {TEAM: TeamRole.LEAD}, True
        case "viewer":
            roles = {TEAM: TeamRole.VIEWER}
        case "admin" | "outsider":
            pass
    ctx = ActorContext(ME, is_admin=relationship == "admin", roles=roles)
    has_owner = status != N
    assignee = (ME if owns else SOMEONE) if has_owner else None
    awaiting = status == A
    item = ItemFacts(
        team_id=TEAM,
        requester_id=requester,
        assignee_id=assignee,
        confidential=False,
        status=status,
        pending_approval_requested_by=SOMEONE if awaiting else None,
    )
    facts = workflow.WorkflowFacts(
        status=status,
        assignee_id=assignee,
        requires_approval=False,
        has_pending_approval=awaiting,
        has_valid_approval=False,
    )
    return ctx, item, facts


@pytest.mark.parametrize("action", list(EXPECTED), ids=lambda a: a.value)
def test_every_status_and_relationship_gets_the_answer_the_spec_gives(action: Action) -> None:
    mismatches: list[str] = []
    for status in ItemStatus:
        for relationship in RELATIONSHIPS:
            ctx, item, facts = persona(relationship, status)
            offered = action in workflow.allowed_actions(ctx, item, facts)
            allowed_in = {LETTERS[c] for c in EXPECTED[action].get(relationship, "")}
            if offered != (status in allowed_in):
                mismatches.append(
                    f"{relationship} at {status.value}: offered={offered}, "
                    f"SPEC says {status in allowed_in}"
                )
    assert mismatches == []


def test_the_table_has_a_row_for_every_action_the_spec_lists() -> None:
    assert set(workflow.TRANSITION_ACTIONS) == set(EXPECTED)


def test_the_transition_table_agrees_with_the_seed_simulators_independent_copy() -> None:
    from scripts.seedlib import sim

    by_sim_name = {Action.APPROVE: "decide", Action.REJECT: "decide"}
    for action in workflow.TRANSITION_ACTIONS:
        name = by_sim_name.get(action, action.value)
        ours = {status for (a, status) in workflow.TRANSITIONS if a == action}
        assert ours == sim.TRANSITIONS[name], action


# ----- people who are neither requester nor plain roles --------------------------------------


def test_the_lead_who_asked_for_an_approval_cannot_approve_or_reject_it_but_can_cancel_it() -> None:
    ctx, item, facts = persona("lead", A)
    mine = ItemFacts(**{**item.__dict__, "pending_approval_requested_by": ME})

    offered = workflow.allowed_actions(ctx, mine, facts)

    assert Action.APPROVE not in offered
    assert Action.REJECT not in offered
    assert Action.CANCEL_APPROVAL in offered


def test_a_member_who_asked_for_an_approval_can_cancel_it_and_nobody_else_of_that_rank() -> None:
    ctx, item, facts = persona("assignee", A)
    mine = ItemFacts(**{**item.__dict__, "pending_approval_requested_by": ME})

    assert Action.CANCEL_APPROVAL in workflow.allowed_actions(ctx, mine, facts)
    assert Action.CANCEL_APPROVAL not in workflow.allowed_actions(ctx, item, facts)


def test_request_approval_is_not_offered_while_one_is_pending() -> None:
    ctx, item, facts = persona("lead_assignee", I)
    waiting = workflow.WorkflowFacts(**{**facts.__dict__, "has_pending_approval": True})

    assert Action.REQUEST_APPROVAL not in workflow.allowed_actions(ctx, item, waiting)


def test_resolve_is_still_offered_without_an_approval_so_the_server_can_say_why_not() -> None:
    ctx, item, facts = persona("assignee", I)
    gated = workflow.WorkflowFacts(**{**facts.__dict__, "requires_approval": True})

    assert Action.RESOLVE in workflow.allowed_actions(ctx, item, gated)


# ----- evaluate: one rule at a time ----------------------------------------------------------


def facts(status: ItemStatus = I, **overrides: Any) -> workflow.WorkflowFacts:
    values: dict[str, Any] = {
        "status": status,
        "assignee_id": None if status == N else SOMEONE,
        "requires_approval": False,
        "has_pending_approval": status == A,
        "has_valid_approval": False,
    }
    values.update(overrides)
    return workflow.WorkflowFacts(**values)


def violation(action: Action, state: workflow.WorkflowFacts, **payload: Any) -> workflow.Violation:
    result = workflow.evaluate(action, state, workflow.Payload(**payload))
    assert isinstance(result, workflow.Violation), result
    return result


def allowed(action: Action, state: workflow.WorkflowFacts, **payload: Any) -> workflow.Allowed:
    result = workflow.evaluate(action, state, workflow.Payload(**payload))
    assert isinstance(result, workflow.Allowed), result
    return result


def test_resolving_a_requires_approval_item_without_a_valid_approval_is_approval_required() -> None:
    result = violation(Action.RESOLVE, facts(requires_approval=True), reason="Refunded")

    assert result.code == "APPROVAL_REQUIRED"
    assert "Request approval" in result.message


def test_resolving_with_a_valid_approval_is_allowed_and_defaults_to_done() -> None:
    result = allowed(
        Action.RESOLVE, facts(requires_approval=True, has_valid_approval=True), reason="Refunded"
    )

    assert (result.to_status, result.resolution) == (R, Resolution.DONE)


def test_resolving_an_item_that_needs_no_approval_ignores_approvals() -> None:
    assert allowed(Action.RESOLVE, facts(), reason="Done").to_status == R


@pytest.mark.parametrize(
    "action",
    [Action.BLOCK, Action.RESOLVE, Action.REOPEN, Action.CLOSE, Action.WITHDRAW, Action.TRANSFER],
)
@pytest.mark.parametrize("blank", [None, "", "   \n"])
def test_a_missing_reason_is_reason_required(action: Action, blank: str | None) -> None:
    status = next(s for (a, s) in workflow.TRANSITIONS if a == action)
    state = facts(status, requires_approval=False)

    result = violation(action, state, reason=blank, resolution=Resolution.DONE)

    assert (result.code, result.field) == ("REASON_REQUIRED", "reason")


def test_a_rejection_needs_a_reason_and_an_approval_needs_none() -> None:
    assert violation(Action.REJECT, facts(A)).code == "REASON_REQUIRED"
    assert allowed(Action.APPROVE, facts(A)).to_status == I


def test_closing_an_open_item_needs_a_resolution() -> None:
    result = violation(Action.CLOSE, facts(I), reason="No longer needed")

    assert (result.code, result.field) == ("VALIDATION_FAILED", "resolution")


def test_closing_a_resolved_item_needs_nothing_and_keeps_its_resolution() -> None:
    result = allowed(Action.CLOSE, facts(R), resolution=Resolution.WONT_DO)

    assert (result.to_status, result.resolution) == (C, None)  # the item keeps its own


def test_a_duplicate_names_the_item_it_duplicates() -> None:
    missing = violation(Action.CLOSE, facts(I), reason="Same", resolution=Resolution.DUPLICATE)
    target = uuid.UUID(int=7)
    named = allowed(
        Action.CLOSE,
        facts(I),
        reason="Same",
        resolution=Resolution.DUPLICATE,
        duplicate_of=target,
    )
    stray = violation(
        Action.CLOSE, facts(I), reason="x", resolution=Resolution.DONE, duplicate_of=target
    )

    assert (missing.code, missing.field) == ("VALIDATION_FAILED", "duplicate_of")
    assert named.duplicate_of == target
    assert (stray.code, stray.field) == ("VALIDATION_FAILED", "duplicate_of")


def test_a_withdrawal_is_always_wont_do() -> None:
    result = allowed(Action.WITHDRAW, facts(N), reason="Raised by mistake")

    assert (result.to_status, result.resolution) == (C, Resolution.WONT_DO)


@pytest.mark.parametrize("action", [Action.RELEASE, Action.ASSIGN, Action.UNASSIGN])
def test_release_assign_and_unassign_say_to_cancel_the_approval_first(action: Action) -> None:
    result = violation(action, facts(A), new_assignee_can_work=True)

    assert result.code == "WORKFLOW_VIOLATION"
    assert "Cancel the approval request first" in result.message


def test_a_second_approval_request_is_already_pending_even_before_the_state_is_judged() -> None:
    state = facts(A)

    assert workflow.pending_conflict(Action.REQUEST_APPROVAL, state) is not None
    assert violation(Action.REQUEST_APPROVAL, state).code == "APPROVAL_ALREADY_PENDING"
    assert workflow.pending_conflict(Action.RESOLVE, state) is None


def test_requesting_approval_from_the_wrong_status_is_a_workflow_violation() -> None:
    assert violation(Action.REQUEST_APPROVAL, facts(N)).code == "WORKFLOW_VIOLATION"


def test_claiming_an_owned_item_is_already_claimed() -> None:
    assert violation(Action.CLAIM, facts(N, assignee_id=SOMEONE)).code == "ALREADY_CLAIMED"


def test_assigning_to_someone_who_cannot_work_the_team_is_refused() -> None:
    result = violation(Action.ASSIGN, facts(I), new_assignee_can_work=False)

    assert result.code == "WORKFLOW_VIOLATION"
    assert allowed(Action.ASSIGN, facts(N), new_assignee_can_work=True).to_status == I
    assert allowed(Action.ASSIGN, facts(B), new_assignee_can_work=True).to_status == B


def test_approving_or_cancelling_with_no_request_waiting_is_refused() -> None:
    state = facts(A, has_pending_approval=False)  # impossible data, but never trust it

    for action in (Action.APPROVE, Action.REJECT, Action.CANCEL_APPROVAL):
        assert violation(action, state, reason="x").code == "WORKFLOW_VIOLATION"


def test_a_violation_becomes_the_domain_error_with_its_field() -> None:
    error = workflow.Violation("REASON_REQUIRED", "Say why.", field="reason").error()

    assert (error.status, error.code) == (422, "REASON_REQUIRED")
    assert error.errors == [{"field": "reason", "message": "Say why.", "type": "value_error"}]
    assert workflow.Violation("APPROVAL_REQUIRED", "x").error().status == 422


def test_every_code_a_violation_uses_is_a_spec_12_error() -> None:
    from app.domain.errors import ALL_ERRORS

    codes = {e.code for e in ALL_ERRORS}
    for code in (
        "WORKFLOW_VIOLATION",
        "ALREADY_CLAIMED",
        "APPROVAL_REQUIRED",
        "APPROVAL_ALREADY_PENDING",
        "REASON_REQUIRED",
        "VALIDATION_FAILED",
    ):
        assert code in codes


# ----- approvals bind to content -------------------------------------------------------------


def approved(
    content: tuple[str, str, str], status: ApprovalStatus = ApprovalStatus.APPROVED
) -> Any:
    return workflow.ApprovalFact(
        uuid.uuid4(), status, subject_hash(TEAM, *content), requested_by=SOMEONE
    )


def test_an_approval_is_valid_only_for_the_content_it_covers() -> None:
    covering = approved(("incident", "Title", "Body"))

    assert workflow.has_valid_approval([covering], subject_hash(TEAM, "incident", "Title", "Body"))
    assert not workflow.has_valid_approval(
        [covering], subject_hash(TEAM, "incident", "Title", "Changed")
    )
    assert not workflow.has_valid_approval(
        [covering], subject_hash(NEW_TEAM, "incident", "Title", "Body")
    )


def test_a_pending_approval_is_not_a_valid_one() -> None:
    pending = approved(("incident", "Title", "Body"), ApprovalStatus.PENDING)

    assert not workflow.has_valid_approval(
        [pending], subject_hash(TEAM, "incident", "Title", "Body")
    )


# ----- what each command plans to write ------------------------------------------------------


def plan(
    action: Action, status: ItemStatus = I, *, state: Any = None, **payload: Any
) -> workflow.TransitionPlan:
    state = state or workflow.TransitionState(status, None if status == N else SOMEONE)
    result = workflow.evaluate(
        action, facts(status, requires_approval=False), workflow.Payload(**payload)
    )
    assert isinstance(result, workflow.Allowed), result
    return workflow.plan_transition(action, state, result, actor_id=ME, now=NOW)


def kinds(result: workflow.TransitionPlan) -> list[EventKind]:
    return [e.kind for e in result.events]


def test_claim_writes_assigned_then_status_changed_like_the_seed_does() -> None:
    result = plan(Action.CLAIM, N)

    assert result.updates == {"assignee_id": ME, "status": I}
    assert kinds(result) == [EventKind.ASSIGNED, EventKind.STATUS_CHANGED]
    assert result.events[0].data == {"assignee": {"from": None, "to": str(ME)}}
    assert result.events[1].data == {"status": {"from": "new", "to": "in_progress"}}


def test_release_clears_the_owner_and_returns_to_new() -> None:
    result = plan(Action.RELEASE, B)

    assert result.updates == {"assignee_id": None, "status": N}
    assert kinds(result) == [EventKind.UNASSIGNED, EventKind.STATUS_CHANGED]
    assert result.events[1].data == {"status": {"from": "blocked", "to": "new"}}


def test_assigning_a_blocked_item_keeps_it_blocked_and_writes_no_status_event() -> None:
    target = uuid.UUID(int=300)
    allowed_ = workflow.evaluate(
        Action.ASSIGN, facts(B), workflow.Payload(new_assignee_can_work=True)
    )
    assert isinstance(allowed_, workflow.Allowed)

    result = workflow.plan_transition(
        Action.ASSIGN,
        workflow.TransitionState(B, SOMEONE),
        allowed_,
        actor_id=ME,
        now=NOW,
        new_assignee_id=target,
    )

    assert result.updates == {"assignee_id": target, "status": B}
    assert kinds(result) == [EventKind.ASSIGNED]


def test_block_carries_the_reason_and_unblock_does_not_need_one() -> None:
    blocked = plan(Action.BLOCK, I, reason="  Waiting on the bank ")

    assert blocked.events[0].reason == "Waiting on the bank"
    assert plan(Action.UNBLOCK, B).events[0].kind == EventKind.UNBLOCKED


def test_resolve_records_the_note_as_the_reason_and_is_a_decision() -> None:
    result = plan(Action.RESOLVE, I, reason="Refund issued")

    assert result.updates == {
        "status": R,
        "resolution": Resolution.DONE,
        "resolution_note": "Refund issued",
        "duplicate_of_id": None,
        "resolved_at": NOW,
    }
    (event,) = result.events
    assert (event.kind, event.reason, event.is_decision) == (
        EventKind.RESOLVED,
        "Refund issued",
        True,
    )


def test_closing_a_resolved_item_keeps_the_resolution_and_always_has_a_reason() -> None:
    plain = plan(Action.CLOSE, R, state=workflow.TransitionState(R, SOMEONE, Resolution.DONE))

    assert plain.updates == {"status": C, "closed_at": NOW}
    assert plain.events[0].reason == "Closed after resolution"  # decision 36
    assert plain.events[0].is_decision


def test_closing_an_open_item_sets_the_resolution_and_the_note() -> None:
    result = plan(Action.CLOSE, I, reason="Out of scope", resolution=Resolution.WONT_DO)

    assert result.updates["resolution"] == Resolution.WONT_DO
    assert result.updates["resolution_note"] == "Out of scope"
    assert result.events[0].data["resolution"] == {"from": None, "to": "wont_do"}


@pytest.mark.parametrize(
    ("owner_can_work", "status", "keeps"),
    [(True, I, True), (False, N, False)],
)
def test_reopen_returns_to_the_previous_owner_if_they_can_still_work_the_team(
    owner_can_work: bool, status: ItemStatus, keeps: bool
) -> None:
    state = workflow.TransitionState(R, SOMEONE, Resolution.DONE)
    verdict = workflow.evaluate(Action.REOPEN, facts(R), workflow.Payload(reason="Not fixed"))
    assert isinstance(verdict, workflow.Allowed)

    result = workflow.plan_transition(
        Action.REOPEN,
        state,
        verdict,
        actor_id=ME,
        now=NOW,
        previous_owner_can_work=owner_can_work,
    )

    assert result.updates["status"] == status
    assert ("assignee_id" in result.updates) is (not keeps)
    assert result.updates["resolution"] is None
    assert result.updates["resolved_at"] is None
    assert result.events[0].data["resolution"] == {"from": "done", "to": None}
    assert result.events[0].is_decision


def test_reopening_something_closed_from_new_goes_back_to_new() -> None:
    state = workflow.TransitionState(C, None, Resolution.WONT_DO)
    verdict = workflow.evaluate(
        Action.REOPEN, facts(C, assignee_id=None), workflow.Payload(reason="x")
    )
    assert isinstance(verdict, workflow.Allowed)

    result = workflow.plan_transition(
        Action.REOPEN, state, verdict, actor_id=ME, now=NOW, previous_owner_can_work=True
    )

    assert result.updates["status"] == N  # no previous owner, however willing the rule
    assert "assignee" not in result.events[0].data


def test_approve_without_a_note_still_has_a_reason_but_never_replaces_a_given_one() -> None:
    state = workflow.TransitionState(A, SOMEONE)
    verdict = workflow.evaluate(Action.APPROVE, facts(A), workflow.Payload())
    assert isinstance(verdict, workflow.Allowed)
    approval_id = uuid.uuid4()

    bare = workflow.plan_transition(
        Action.APPROVE, state, verdict, actor_id=ME, now=NOW, approval_id=approval_id
    )
    noted = workflow.plan_transition(
        Action.APPROVE,
        state,
        verdict,
        actor_id=ME,
        now=NOW,
        approval_id=approval_id,
        note="Looks right",
    )

    assert bare.events[0].reason == "Approved"
    assert noted.events[0].reason == "Looks right"
    assert bare.events[0].data["approval_id"] == str(approval_id)
    assert bare.events[0].is_decision


def test_rejecting_keeps_the_given_reason() -> None:
    state = workflow.TransitionState(A, SOMEONE)
    verdict = workflow.evaluate(Action.REJECT, facts(A), workflow.Payload(reason="Wrong amount"))
    assert isinstance(verdict, workflow.Allowed)

    result = workflow.plan_transition(
        Action.REJECT, state, verdict, actor_id=ME, now=NOW, approval_id=uuid.uuid4()
    )

    assert (result.events[0].kind, result.events[0].reason) == (
        EventKind.APPROVAL_REJECTED,
        "Wrong amount",
    )


# ----- transfer ------------------------------------------------------------------------------


def transfer(
    status: ItemStatus,
    *,
    owner_stays: bool,
    approvals: list[Any] | None = None,
    assignee: uuid.UUID | None = SOMEONE,
) -> workflow.TransferPlan:
    return workflow.plan_transfer(
        workflow.TransitionState(status, assignee),
        from_team_id=TEAM,
        to_team_id=NEW_TEAM,
        assignee_can_work_there=owner_stays,
        approvals=approvals or [],
        reason="Wrong team",
    )


def test_a_transfer_keeps_an_owner_who_can_work_the_new_team() -> None:
    result = transfer(I, owner_stays=True)

    assert result.updates == {"team_id": NEW_TEAM}
    (event,) = result.events
    assert event.kind == EventKind.TRANSFERRED
    assert event.is_decision
    assert event.data == {"team": {"from": str(TEAM), "to": str(NEW_TEAM)}}


def test_a_transfer_unassigns_an_owner_who_cannot_and_goes_back_to_new() -> None:
    result = transfer(B, owner_stays=False)

    assert result.updates == {"team_id": NEW_TEAM, "assignee_id": None, "status": N}
    assert result.events[0].data["assignee"] == {"from": str(SOMEONE), "to": None}
    assert result.events[0].data["status"] == {"from": "blocked", "to": "new"}


def test_a_transfer_of_a_new_item_changes_only_the_team() -> None:
    result = transfer(N, owner_stays=False, assignee=None)

    assert result.updates == {"team_id": NEW_TEAM}


def test_a_transfer_cancels_a_pending_approval_before_it_moves_the_item() -> None:
    pending = workflow.ApprovalFact(
        uuid.uuid4(), ApprovalStatus.PENDING, subject_hash(TEAM, "incident", "T", "B"), SOMEONE
    )

    result = transfer(A, owner_stays=True, approvals=[pending])

    assert [e.kind for e in result.events] == [EventKind.APPROVAL_CANCELLED, EventKind.TRANSFERRED]
    assert result.events[0].reason == "transferred"
    assert result.events[0].data["status"] == {"from": "awaiting_approval", "to": "in_progress"}
    assert result.updates["status"] == I
    assert result.cancelled == pending.id


def test_a_transfer_of_an_awaiting_item_whose_owner_cannot_follow_ends_in_new() -> None:
    pending = workflow.ApprovalFact(
        uuid.uuid4(), ApprovalStatus.PENDING, subject_hash(TEAM, "incident", "T", "B"), SOMEONE
    )

    result = transfer(A, owner_stays=False, approvals=[pending])

    assert result.updates["status"] == N
    assert result.events[1].data["status"] == {"from": "in_progress", "to": "new"}


def test_a_transfer_invalidates_every_approved_approval_because_the_team_is_part_of_the_hash() -> (
    None
):
    first = workflow.ApprovalFact(uuid.uuid4(), ApprovalStatus.APPROVED, "h1", SOMEONE)
    second = workflow.ApprovalFact(uuid.uuid4(), ApprovalStatus.APPROVED, "h2", SOMEONE)

    result = transfer(I, owner_stays=True, approvals=[first, second])

    assert set(result.invalidated) == {first.id, second.id}
    assert result.events[-1].kind == EventKind.APPROVAL_INVALIDATED
    assert result.events[-1].data["fields"] == ["team"]
    assert result.invalidated_reason == "Content changed: team"
