"""Checks that seeded histories are valid, by replaying events from the database.

Deliberately independent of sim.py: it has its own, event-kind-based encoding of SPEC 4.1 and
reads nothing but what is stored. For each item it replays `item_events` in order, applies every
{field: {from, to}} change, and checks after each event that
- the change starts from the state the previous events left (no gaps, no jumps);
- the status change is one that event kind may cause;
- the intermediate state obeys the database rules (an active item has an owner, ...);
- versions are contiguous and the last one equals work_items.version;
and at the end that the replayed state equals the stored row. It also checks the approvals.
"""

import json
import random
import uuid
from datetime import datetime
from typing import Any

import asyncpg

TRACKED = [
    "team",
    "type",
    "title",
    "description",
    "priority",
    "status",
    "resolution",
    "assignee",
    "duplicate_of",
    "confidential",
    "requires_approval",
    "due_at",
    "due_source",
]
ACTIVE = {"in_progress", "blocked", "awaiting_approval"}
OPEN = {"new", "in_progress", "blocked", "awaiting_approval"}

# kind -> allowed (from, to) status pairs. A kind missing here must not change status.
STATUS_RULES: dict[str, set[tuple[str, str]]] = {
    "created": {("None", "new")},
    "status_changed": {
        ("new", "in_progress"),
        ("in_progress", "new"),
        ("blocked", "new"),
    },
    "blocked": {("in_progress", "blocked")},
    "unblocked": {("blocked", "in_progress")},
    "approval_requested": {("in_progress", "awaiting_approval")},
    "approval_approved": {("awaiting_approval", "in_progress")},
    "approval_rejected": {("awaiting_approval", "in_progress")},
    "approval_cancelled": {("awaiting_approval", "in_progress")},
    "approval_invalidated": {("awaiting_approval", "in_progress")},
    "resolved": {("in_progress", "resolved")},
    "reopened": {
        ("resolved", "in_progress"),
        ("resolved", "new"),
        ("closed", "in_progress"),
        ("closed", "new"),
    },
    "closed": {
        ("new", "closed"),
        ("in_progress", "closed"),
        ("blocked", "closed"),
        ("resolved", "closed"),
    },
    "transferred": {("in_progress", "new"), ("blocked", "new")},
}
# These may legitimately leave the status alone (nothing pending; owner still valid).
KEEPS_STATUS_OK = {"approval_invalidated", "transferred"}
SYSTEM_KINDS = {"sla_breached", "duplicate_suggested"}
# Events that never change the item's version (SPEC 6.2): they carry the current one.
NON_BUMPING = {"commented", "sla_breached", "duplicate_suggested"}
ALWAYS_DECISIONS = {
    "resolved",
    "reopened",
    "closed",
    "transferred",
    "approval_approved",
    "approval_rejected",
    "approval_invalidated",
    "confidential_changed",
}
APPROVAL_STATUS_BY_KIND = {
    "approval_requested": "pending",
    "approval_approved": "approved",
    "approval_rejected": "rejected",
    "approval_cancelled": "cancelled",
    "approval_invalidated": "invalidated",
}
MATERIAL = {"title", "description", "type", "team"}


def _ref(value: Any) -> Any:
    return str(value) if isinstance(value, uuid.UUID) else value


Row = Any  # an asyncpg.Record from the database, or a dict in unit tests


def check_item(
    item: Row, events: list[Row], approvals: list[Row], reads: list[Row] | None = None
) -> list[str]:
    key = item["key"]
    problems: list[str] = []

    def bad(message: str) -> None:
        problems.append(f"{key}: {message}")

    if not events:
        return [f"{key}: has no events"]

    state: dict[str, Any] = dict.fromkeys(TRACKED)
    approved_valid = False
    version = 0
    previous_kind = ""
    command_events: list[Row] = []
    sla_at: datetime | None = None
    approval_state: dict[str, str] = {}
    resolved_at: datetime | None = None
    closed_at: datetime | None = None

    for n, event in enumerate(events):
        kind = event["kind"]
        data = json.loads(event["data"]) if isinstance(event["data"], str) else event["data"]
        at_version = event["item_version"]
        if n == 0:
            if kind != "created" or at_version != 1:
                bad(f"first event is {kind} v{at_version}, expected created v1")
        elif kind in NON_BUMPING:
            if at_version != version:
                bad(f"{kind} (event {event['id']}) has v{at_version}, current is v{version}")
        elif at_version not in (version, version + 1):
            bad(f"version jumps from {version} to {at_version} at event {event['id']}")
        elif at_version == version and previous_kind in NON_BUMPING:
            bad(f"{kind} (event {event['id']}) changed the item but reused v{version}")
        version = at_version
        previous_kind = kind

        before_status = str(state["status"])
        for name, delta in data.items():
            if name not in TRACKED or not isinstance(delta, dict) or set(delta) != {"from", "to"}:
                continue
            if state[name] != delta["from"]:
                bad(f"{kind}: {name} changes from {delta['from']!r} but was {state[name]!r}")
            state[name] = delta["to"]
            if name in MATERIAL:
                approved_valid = False

        after_status = str(state["status"])
        if before_status != after_status:
            if (before_status, after_status) not in STATUS_RULES.get(kind, set()):
                bad(f"{kind} cannot change status {before_status} -> {after_status}")
        elif kind in STATUS_RULES and kind not in KEEPS_STATUS_OK:
            bad(f"{kind} did not change status")

        # A command writes several events at one version; only the state after the last one is
        # ever observable, so the database rules are checked there, not between its events.
        ends_command = n == len(events) - 1 or events[n + 1]["item_version"] != at_version
        status, assignee = state["status"], state["assignee"]
        if ends_command and status in ACTIVE and assignee is None:
            bad(f"after {kind} (event {event['id']}) status {status} has no owner")
        if ends_command and status in ("resolved", "closed") and state["resolution"] is None:
            bad(f"after {kind} status {status} has no resolution")
        if ends_command and state["resolution"] == "duplicate" and state["duplicate_of"] is None:
            bad(f"after {kind} resolution duplicate has no target")

        command_events.append(event)
        if ends_command:
            # Every event of a command carries the team the item has when the command ends.
            for written in command_events:
                if str(written["team_id"]) != state["team"]:
                    bad(f"{written['kind']} event {written['id']} lacks the item's team")
            command_events = []
        if (event["actor_id"] is None) != (kind in SYSTEM_KINDS):
            bad(f"{kind} has the wrong actor ({event['actor_id']})")
        expected_decision = kind in ALWAYS_DECISIONS
        if kind == "priority_changed":
            delta = data["priority"]
            expected_decision = delta["to"] > delta["from"] and delta["from"] <= 1
        if kind == "requires_approval_changed":
            expected_decision = data["requires_approval"]["to"] is False
        if event["is_decision"] != expected_decision:
            bad(f"{kind} has is_decision={event['is_decision']}, expected {expected_decision}")
        if event["is_decision"] and not (event["reason"] or "").strip():
            bad(f"decision {kind} has no reason")

        if kind == "approval_approved":
            approved_valid = True
        if kind == "resolved":
            resolved_at = event["created_at"]
            if state["requires_approval"] and not approved_valid:
                bad("resolved although approval was required and none was valid")
        if kind == "closed":
            closed_at = event["created_at"]
        if kind == "reopened":
            resolved_at = closed_at = None
        if kind == "sla_breached":
            if sla_at is not None:
                bad("sla_breached twice")
            sla_at = event["created_at"]
        referenced = [data.get("approval_id"), *data.get("approval_ids", [])]
        for approval_id in filter(None, referenced):
            approval_state[approval_id] = APPROVAL_STATUS_BY_KIND[kind]

    last = events[-1]
    expectations = {
        "version": version,
        "last_event_id": last["id"],
        "updated_at": last["created_at"],
        "created_at": events[0]["created_at"],
        "status": state["status"],
        "assignee_id": state["assignee"],
        "priority": state["priority"],
        "type": state["type"],
        "title": state["title"],
        "description": state["description"],
        "resolution": state["resolution"],
        "duplicate_of_id": _ref(state["duplicate_of"]),
        "confidential": state["confidential"],
        "requires_approval": state["requires_approval"],
        "due_source": state["due_source"],
        "team_id": state["team"],
        "sla_breached_at": sla_at,
        "resolved_at": resolved_at,
        "closed_at": closed_at,
    }
    for column, expected in expectations.items():
        actual = item[column]
        if isinstance(actual, uuid.UUID):
            actual = str(actual)
        if actual != expected:
            bad(f"stored {column}={actual!r} but the history says {expected!r}")
    if item["due_at"] != _parse(state["due_at"]):
        bad(f"stored due_at={item['due_at']!r} but the history says {state['due_at']!r}")

    event_ids = {e["id"] for e in events}
    for read in reads or []:
        if read["last_read_event_id"] not in event_ids:
            marker = read["last_read_event_id"]
            bad(f"user {read['user_id']} has read up to event {marker}, which is not this item's")

    pending = [a for a in approvals if a["status"] == "pending"]
    if (item["status"] == "awaiting_approval") != bool(pending):
        bad("status awaiting_approval and the pending approval disagree")
    for approval in approvals:
        expected_status = approval_state.get(str(approval["id"]))
        if expected_status != approval["status"]:
            bad(f"approval {approval['id']} is {approval['status']}, events say {expected_status}")
    return problems


def _parse(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


async def verify_histories(
    conn: asyncpg.Connection, *, sample: int | None = None, seed: int = 0
) -> tuple[int, list[str]]:
    """Check `sample` random items (all when None). Returns (items checked, problems)."""
    ids = [r["id"] for r in await conn.fetch("SELECT id FROM work_items")]
    if sample is not None and sample < len(ids):
        ids = random.Random(seed).sample(ids, sample)  # noqa: S311
    items = {
        r["id"]: r for r in await conn.fetch("SELECT * FROM work_items WHERE id = ANY($1)", ids)
    }
    events: dict[uuid.UUID, list[asyncpg.Record]] = {i: [] for i in ids}
    for r in await conn.fetch("SELECT * FROM item_events WHERE item_id = ANY($1) ORDER BY id", ids):
        events[r["item_id"]].append(r)
    approvals: dict[uuid.UUID, list[asyncpg.Record]] = {i: [] for i in ids}
    for r in await conn.fetch("SELECT * FROM approvals WHERE item_id = ANY($1)", ids):
        approvals[r["item_id"]].append(r)

    reads: dict[uuid.UUID, list[asyncpg.Record]] = {i: [] for i in ids}
    for r in await conn.fetch("SELECT * FROM item_reads WHERE item_id = ANY($1)", ids):
        reads[r["item_id"]].append(r)

    problems: list[str] = []
    for item_id in ids:
        problems.extend(
            check_item(items[item_id], events[item_id], approvals[item_id], reads[item_id])
        )
    return len(ids), problems
