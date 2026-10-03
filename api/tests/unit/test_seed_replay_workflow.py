"""Every seeded history replays through `workflow.evaluate` without a violation (SPEC 13.1)."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from scripts.seedlib import generate as g
from tests.support.replay import replay_item

from .seed_records import records_from_dataset

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("seed", [1, 2, 3, 7, 42, 2026])
def test_every_seeded_history_replays_through_the_workflow_rules(seed: int) -> None:
    dataset = g.build_dataset("demo", seed=seed, now=NOW, password_hash="x")
    items, events, _, _ = records_from_dataset(dataset)

    problems = [p for i in items for p in replay_item(events[i], items[i]["key"])]

    assert problems == []
    assert len(items) == 600


def history(*events: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"id": n, "actor_id": None, "reason": None, **e} for n, e in enumerate(events, 1)]


REQUESTER = str(uuid.UUID(int=10))
OWNER = str(uuid.UUID(int=11))


def delta(value: object) -> dict[str, object]:
    return {"from": None, "to": value}


def created() -> dict[str, Any]:
    return {
        "kind": "created",
        "item_version": 1,
        "actor_id": REQUESTER,
        "data": {
            "team": delta(str(uuid.UUID(int=1))),
            "type": delta("incident"),
            "title": delta("Title"),
            "description": delta("Body"),
            "status": delta("new"),
            "requires_approval": delta(False),
        },
    }


def test_the_replay_catches_a_resolve_that_skipped_a_required_approval() -> None:
    first = created()
    first["data"]["requires_approval"] = {"from": None, "to": True}
    broken = history(
        first,
        {
            "kind": "assigned",
            "item_version": 2,
            "actor_id": OWNER,
            "data": {"assignee": delta(OWNER)},
        },
        {
            "kind": "status_changed",
            "item_version": 2,
            "actor_id": OWNER,
            "data": {"status": {"from": "new", "to": "in_progress"}},
        },
        {
            "kind": "resolved",
            "item_version": 3,
            "actor_id": OWNER,
            "reason": "Done",
            "is_decision": True,
            "data": {
                "status": {"from": "in_progress", "to": "resolved"},
                "resolution": {"from": None, "to": "done"},
            },
        },
    )

    problems = replay_item(broken, "X-1")

    assert len(problems) == 1
    assert "resolve refused" in problems[0]
    assert "approval" in problems[0]


def test_the_replay_catches_a_jump_the_table_does_not_allow() -> None:
    broken = history(
        created(),
        {
            "kind": "blocked",
            "item_version": 2,
            "actor_id": OWNER,
            "reason": "Waiting",
            "data": {"status": {"from": "new", "to": "blocked"}},
        },
    )

    problems = replay_item(broken, "X-1")

    assert len(problems) == 1
    assert "block refused from new" in problems[0]
