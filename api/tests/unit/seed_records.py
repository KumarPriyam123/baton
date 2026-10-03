"""Turns in-memory seed data into the dict-shaped rows that scripts.seedlib.verify reads."""

import json
import uuid
from typing import Any, cast

from scripts.seedlib import generate as g
from scripts.seedlib.sim import ItemSim

Records = tuple[
    dict[uuid.UUID, dict[str, Any]],
    dict[uuid.UUID, list[dict[str, Any]]],
    dict[uuid.UUID, list[dict[str, Any]]],
]


def records_from_dataset(ds: g.Dataset) -> Records:
    items: dict[uuid.UUID, dict[str, Any]] = {
        cast(uuid.UUID, row[0]): dict(zip(g.ITEM_COLS, row, strict=True)) for row in ds.items
    }
    events: dict[uuid.UUID, list[dict[str, Any]]] = {i: [] for i in items}
    for position, row in enumerate(ds.events, start=1):  # COPY order is id order
        event: dict[str, Any] = dict(zip(g.EVENT_COLS, row, strict=True)) | {"id": position}
        events[event["item_id"]].append(event)
    for item_id, history in events.items():
        items[item_id]["last_event_id"] = history[-1]["id"]
    approvals: dict[uuid.UUID, list[dict[str, Any]]] = {i: [] for i in items}
    for row in ds.approvals:
        approval: dict[str, Any] = dict(zip(g.APPROVAL_COLS, row, strict=True))
        approvals[approval["item_id"]].append(approval)
    return items, events, approvals


def records_from_sim(
    sim: ItemSim,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """One simulated item as (item row, event rows, approval rows), as the database holds it."""
    events = [
        {
            "id": n,
            "item_id": sim.id,
            "team_id": e.team_id,
            "actor_id": e.actor,
            "kind": e.kind.value,
            "item_version": e.version,
            "data": json.dumps(e.data),
            "reason": e.reason,
            "is_decision": e.is_decision,
            "created_at": e.at,
        }
        for n, e in enumerate(sim.events, start=1)
    ]
    item = {
        "key": sim.key,
        "version": sim.version,
        "last_event_id": events[-1]["id"],
        "updated_at": sim.updated_at,
        "created_at": sim.created_at,
        "status": sim.status.value,
        "assignee_id": sim.assignee,
        "priority": sim.priority,
        "type": sim.type,
        "title": sim.title,
        "description": sim.description,
        "resolution": sim.resolution.value if sim.resolution else None,
        "duplicate_of_id": sim.duplicate_of,
        "confidential": sim.confidential,
        "requires_approval": sim.requires_approval,
        "due_source": sim.due_source.value,
        "team_id": sim.team.id,
        "sla_breached_at": sim.sla_breached_at,
        "resolved_at": sim.resolved_at,
        "closed_at": sim.closed_at,
        "due_at": sim.due_at,
    }
    approvals = [{"id": a.id, "status": a.status.value} for a in sim.approvals]
    return item, events, approvals
