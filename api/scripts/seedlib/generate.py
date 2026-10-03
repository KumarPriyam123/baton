"""Builds a complete, deterministic seed dataset in memory (no database access).

The same (size, seed, now) always gives the same data. Rows are plain tuples in the column order
that load.py copies into Postgres.
"""

import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import cast

from app.domain.enums import ItemType

from . import text
from .personas import (
    DEMO_PERSONAS,
    DEMO_TEAMS,
    EMAIL_DOMAIN,
    EXTRA_TEAMS,
    FIRST_NAMES,
    LAST_NAMES,
    TeamSpec,
)
from .sim import TARGETS, ItemSim, Runner, TeamCtx

REQUEST_ID = "seed"

USER_COLS = ("id", "email", "name", "password_hash", "is_admin", "created_at")
TEAM_COLS = ("id", "key", "name", "description", "created_at")
MEMBERSHIP_COLS = ("team_id", "user_id", "role", "created_at")
ITEM_COLS = [
    "id",
    "key",
    "team_id",
    "number",
    "origin_team_id",
    "type",
    "title",
    "description",
    "priority",
    "status",
    "resolution",
    "resolution_note",
    "duplicate_of_id",
    "requester_id",
    "assignee_id",
    "confidential",
    "requires_approval",
    "due_at",
    "due_source",
    "sla_breached_at",
    "last_activity_at",
    "version",
    "created_at",
    "updated_at",
    "resolved_at",
    "closed_at",
]
EVENT_COLS = [
    "item_id",
    "team_id",
    "actor_id",
    "kind",
    "item_version",
    "data",
    "reason",
    "is_decision",
    "request_id",
    "created_at",
]
COMMENT_COLS = ("item_id", "author_id", "body", "created_at")
APPROVAL_COLS = [
    "id",
    "item_id",
    "status",
    "requested_by",
    "requested_at",
    "request_note",
    "subject_hash",
    "decided_by",
    "decided_at",
    "decision_note",
    "invalidated_reason",
]
WATCHER_COLS = ("item_id", "user_id", "created_at")
# last_read_event_id holds the event's 1-based position in Dataset.events; load.py turns it
# into an id.
READ_COLS = ("user_id", "item_id", "last_read_event_id", "read_at")
SIMILAR_COLS = ("item_id", "similar_item_id", "score", "created_at")


@dataclass(frozen=True)
class SizeSpec:
    name: str
    users: int  # total, including the named personas
    extra_teams: int
    items: int
    window_days: int  # how far back finished work reaches
    dwell: tuple[int, int]  # extra "things happen" steps per item (min, max)
    comment_rate: float
    dwell_comment_share: float  # of dwell steps, how many are comments
    noise: float
    transfer_weight: float
    notification_days: int


SIZES = {
    "demo": SizeSpec(
        "demo",
        users=len(DEMO_PERSONAS),
        extra_teams=0,
        items=600,
        window_days=75,
        dwell=(1, 6),
        comment_rate=0.2,
        dwell_comment_share=0.55,
        noise=0.2,
        transfer_weight=0.6,
        notification_days=14,
    ),
    "large": SizeSpec(
        "large",
        users=2000,
        extra_teams=len(EXTRA_TEAMS),
        items=50_000,
        window_days=365,
        dwell=(5, 15),
        comment_rate=0.1,
        dwell_comment_share=0.25,
        noise=0.6,
        transfer_weight=0.6,
        notification_days=1,
    ),
}

TARGET_WEIGHTS = {
    "new": 10,
    "in_progress": 14,
    "blocked": 5,
    "awaiting": 4,
    "resolved": 15,
    "closed_done": 33,
    "closed_other": 16,
    "reopened": 3,
}
assert set(TARGET_WEIGHTS) == set(TARGETS)

TYPE_WEIGHTS: dict[str, dict[str, int]] = {
    "PAY": {
        "payment_investigation": 45,
        "customer_issue": 20,
        "ops_task": 15,
        "engineering": 10,
        "incident": 10,
    },
    "SUP": {"customer_issue": 60, "incident": 10, "payment_investigation": 10, "ops_task": 20},
    "SRE": {"incident": 45, "engineering": 20, "ops_task": 35},
    "CMP": {"compliance_request": 80, "ops_task": 20},
    "FIN": {
        "ops_task": 45,
        "payment_investigation": 35,
        "customer_issue": 10,
        "compliance_request": 10,
    },
    "ENG": {"engineering": 60, "incident": 20, "ops_task": 10, "customer_issue": 10},
}
TEAM_WEIGHTS = {"PAY": 22, "SUP": 22, "SRE": 16, "CMP": 10, "FIN": 14, "ENG": 16}
PRIORITY_WEIGHTS = [5, 20, 50, 25]
INCIDENT_PRIORITY_WEIGHTS = [10, 30, 40, 20]
ALL_TYPES = [t.value for t in ItemType]


@dataclass
class Dataset:
    size: str
    now: datetime
    users: list[tuple[object, ...]] = field(default_factory=list)
    teams: list[tuple[object, ...]] = field(default_factory=list)
    memberships: list[tuple[object, ...]] = field(default_factory=list)
    items: list[tuple[object, ...]] = field(default_factory=list)
    events: list[tuple[object, ...]] = field(default_factory=list)
    comments: list[tuple[object, ...]] = field(default_factory=list)
    approvals: list[tuple[object, ...]] = field(default_factory=list)
    watchers: list[tuple[object, ...]] = field(default_factory=list)
    reads: list[tuple[object, ...]] = field(default_factory=list)
    similar: list[tuple[object, ...]] = field(default_factory=list)
    user_ids_by_email: dict[str, uuid.UUID] = field(default_factory=dict)

    def counts(self) -> dict[str, int]:
        return {
            "users": len(self.users),
            "teams": len(self.teams),
            "memberships": len(self.memberships),
            "items": len(self.items),
            "events": len(self.events),
            "comments": len(self.comments),
            "approvals": len(self.approvals),
            "watchers": len(self.watchers),
            "item_reads": len(self.reads),
            "item_similar": len(self.similar),
        }


def _uuid(rng: random.Random) -> uuid.UUID:
    return uuid.UUID(int=rng.getrandbits(128), version=4)


def _weighted(rng: random.Random, weights: dict[str, int]) -> str:
    return rng.choices(list(weights), list(weights.values()))[0]


def build_dataset(size: str, *, seed: int, now: datetime, password_hash: str) -> Dataset:
    spec = SIZES[size]
    rng = random.Random(seed)  # noqa: S311 - reproducible fake data, not security
    data = Dataset(size=size, now=now)
    base = now - timedelta(days=spec.window_days + 60)

    teams, user_ids = _people(rng, spec, data, base, password_hash)
    _items(rng, spec, data, teams, user_ids, now)
    return data


def _people(
    rng: random.Random, spec: SizeSpec, data: Dataset, base: datetime, password_hash: str
) -> tuple[list[TeamCtx], list[uuid.UUID]]:
    team_specs: list[TeamSpec] = [*DEMO_TEAMS, *EXTRA_TEAMS[: spec.extra_teams]]
    teams: dict[str, TeamCtx] = {}
    for t in team_specs:
        ctx = TeamCtx(id=_uuid(rng), key=t.key, name=t.name)
        teams[t.key] = ctx
        data.teams.append((ctx.id, t.key, t.name, t.description, base))

    user_ids: list[uuid.UUID] = []
    for persona in DEMO_PERSONAS:
        uid = _uuid(rng)
        user_ids.append(uid)
        data.user_ids_by_email[persona.email] = uid
        data.users.append((uid, persona.email, persona.name, password_hash, persona.is_admin, base))
        for team_key, role in persona.memberships:
            _add_member(data, teams[team_key], uid, role, base)

    generated: list[uuid.UUID] = []
    for n in range(spec.users - len(DEMO_PERSONAS)):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        uid = _uuid(rng)
        email = f"{first}.{last}{n + 1}@{EMAIL_DOMAIN}".lower()
        data.users.append((uid, email, f"{first} {last}", password_hash, False, base))
        generated.append(uid)
    user_ids.extend(generated)

    if generated:
        for ctx in teams.values():
            is_extra = ctx.key not in TEAM_WEIGHTS
            lead_count = 3 if is_extra else 0
            member_count = rng.randint(30, 70) if is_extra else rng.randint(15, 30)
            viewer_count = rng.randint(5, 15)
            picked = rng.sample(generated, lead_count + member_count + viewer_count)
            for uid in picked[:lead_count]:
                _add_member(data, ctx, uid, "lead", base)
            for uid in picked[lead_count : lead_count + member_count]:
                _add_member(data, ctx, uid, "member", base)
            for uid in picked[lead_count + member_count :]:
                _add_member(data, ctx, uid, "viewer", base)

    ordered = list(teams.values())
    for ctx in ordered:
        assert len(ctx.leads) >= 2, f"team {ctx.key} needs two leads for four-eyes approvals"
        ctx.finish()
    return ordered, user_ids


def _add_member(data: Dataset, ctx: TeamCtx, uid: uuid.UUID, role: str, at: datetime) -> None:
    data.memberships.append((ctx.id, uid, role, at))
    {"lead": ctx.leads, "member": ctx.members, "viewer": ctx.viewers}[role].append(uid)


@dataclass
class _Plan:
    created_at: datetime
    team: TeamCtx
    target: str


def _plan_items(
    rng: random.Random, spec: SizeSpec, teams: list[TeamCtx], now: datetime
) -> list[_Plan]:
    if spec.extra_teams:
        # Demo teams stay busiest; the rest follow a long tail.
        weights = [
            TEAM_WEIGHTS.get(t.key, 0) or max(1.0, 14.0 / (i - 4)) for i, t in enumerate(teams)
        ]
    else:
        weights = [float(TEAM_WEIGHTS[t.key]) for t in teams]
    plans: list[_Plan] = []
    for _ in range(spec.items):
        team = rng.choices(teams, weights)[0]
        target = _weighted(rng, TARGET_WEIGHTS)
        if target in ("new", "in_progress", "blocked", "awaiting"):
            # Most open work is recent; a minority has been sitting there (overdue, going quiet).
            if rng.random() < 0.88:
                age = timedelta(hours=rng.uniform(0.2, 72))
            else:
                age = timedelta(days=rng.uniform(4, min(40, spec.window_days)))
        else:
            age = timedelta(days=rng.uniform(1.5, spec.window_days))
        plans.append(_Plan(now - age, team, target))
    plans.sort(key=lambda p: p.created_at)
    return plans


def _items(
    rng: random.Random,
    spec: SizeSpec,
    data: Dataset,
    teams: list[TeamCtx],
    user_ids: list[uuid.UUID],
    now: datetime,
) -> None:
    plans = _plan_items(rng, spec, teams, now)
    routable = [t for t in teams if len(t.leads) >= 2]
    earlier: dict[tuple[uuid.UUID, str], list[tuple[uuid.UUID, str, str]]] = {}
    by_team: dict[uuid.UUID, list[uuid.UUID]] = {}
    requester_weights = _requester_weights(data, user_ids)
    viewer_watchers = _named_viewers(data, teams)
    event_rows: list[tuple[datetime, int, tuple[object, ...]]] = []
    seq = 0
    read_seqs: list[tuple[uuid.UUID, uuid.UUID, int, datetime]] = []

    for plan in plans:
        team = plan.team
        weights = TYPE_WEIGHTS.get(team.key) or dict.fromkeys(ALL_TYPES, 1)
        item_type = _weighted(rng, weights)
        title, description = text.make_title_and_description(rng, item_type)
        priority = rng.choices(
            range(4), INCIDENT_PRIORITY_WEIGHTS if item_type == "incident" else PRIORITY_WEIGHTS
        )[0]
        requires_approval = item_type in ("payment_investigation", "compliance_request") or (
            item_type == "ops_task" and rng.random() < 0.15
        )
        sim = ItemSim(
            rng,
            team=team,
            item_type=item_type,
            title=title,
            description=description,
            priority=priority,
            requester=rng.choices(user_ids, requester_weights)[0],
            confidential=item_type == "compliance_request",
            requires_approval=requires_approval,
            created_at=plan.created_at,
        )

        candidates = earlier.setdefault((team.id, item_type), [])
        if candidates and rng.random() < 0.08:
            picked = rng.sample(candidates[-30:], min(len(candidates), rng.randint(1, 3)))
            at = sim.created_at + timedelta(seconds=rng.randint(20, 90))
            if at <= data.now:
                sim.suggest_duplicates(at, [key for _, key, _ in picked])
                sim.similar = [
                    (other_id, round(rng.uniform(0.4, 0.9), 3)) for other_id, _, _ in picked
                ]

        runner = Runner(
            sim,
            rng,
            now,
            noise=spec.noise,
            comment_rate=spec.comment_rate,
            other_teams=routable,
            duplicate_candidates=by_team.get(team.id, [])[-40:],
            transfer_weight=spec.transfer_weight,
            dwell_steps=rng.randint(*spec.dwell),
            dwell_comment_share=spec.dwell_comment_share,
        )
        runner.run(plan.target)

        candidates.append((sim.id, sim.key, sim.title))
        by_team.setdefault(team.id, []).append(sim.id)
        seq = _emit_rows(rng, data, sim, event_rows, seq, viewer_watchers, read_seqs)

    event_rows.sort(key=lambda r: (r[0], r[1]))
    data.events = [row for _, _, row in event_rows]
    position = {event_seq: n for n, (_, event_seq, _) in enumerate(event_rows, start=1)}
    data.reads = [(user, item, position[s], at) for user, item, s, at in read_seqs]
    data.comments.sort(key=lambda r: cast(datetime, r[3]))


def _requester_weights(data: Dataset, user_ids: list[uuid.UUID]) -> list[float]:
    """Support agents raise the most requests (SPEC 13.1: Meera is a frequent requester)."""
    frequent = {data.user_ids_by_email[f"{local}@{EMAIL_DOMAIN}"] for local in ("meera", "kavya")}
    return [6.0 if uid in frequent else 1.0 for uid in user_ids]


def _named_viewers(data: Dataset, teams: list[TeamCtx]) -> dict[uuid.UUID, list[uuid.UUID]]:
    """team id -> named personas who are viewers there and follow some of its items."""
    named = set(data.user_ids_by_email.values())
    return {
        t.id: [v for v in t.viewers if v in named]
        for t in teams
        if any(v in named for v in t.viewers)
    }


def _emit_rows(
    rng: random.Random,
    data: Dataset,
    sim: ItemSim,
    event_rows: list[tuple[datetime, int, tuple[object, ...]]],
    seq: int,
    viewer_watchers: dict[uuid.UUID, list[uuid.UUID]],
    read_seqs: list[tuple[uuid.UUID, uuid.UUID, int, datetime]],
) -> int:
    data.items.append(
        (
            sim.id, sim.key, sim.team.id, sim.number, sim.origin.id, sim.type, sim.title,
            sim.description, sim.priority, sim.status.value,
            sim.resolution.value if sim.resolution else None, sim.resolution_note,
            sim.duplicate_of, sim.requester, sim.assignee, sim.confidential,
            sim.requires_approval, sim.due_at, sim.due_source.value, sim.sla_breached_at,
            sim.last_activity_at, sim.version, sim.created_at, sim.updated_at,
            sim.resolved_at, sim.closed_at,
        )
    )  # fmt: skip
    first_seq = seq + 1
    for e in sim.events:
        seq += 1
        row = (
            sim.id, e.team_id, e.actor, e.kind.value, e.version,
            json.dumps(e.data, separators=(",", ":")), e.reason, e.is_decision, REQUEST_ID, e.at,
        )  # fmt: skip
        event_rows.append((e.at, seq, row))
    for c in sim.comments:
        data.comments.append((sim.id, c.author, c.body, c.at))
    for a in sim.approvals:
        data.approvals.append(
            (
                a.id, sim.id, a.status.value, a.requested_by, a.requested_at, a.request_note,
                a.subject_hash, a.decided_by, a.decided_at, a.decision_note, a.invalidated_reason,
            )
        )  # fmt: skip

    watchers = set(sim.watchers)
    for viewer in viewer_watchers.get(sim.team.id, []):
        if not sim.confidential and rng.random() < 0.06:
            watchers.add(viewer)
    for uid in watchers:
        data.watchers.append((sim.id, uid, sim.created_at))

    for sim_other, score in sim.similar:
        data.similar.append((sim.id, sim_other, score, sim.created_at + timedelta(seconds=60)))

    _reads(rng, data, sim, first_seq, read_seqs)
    return seq


def _reads(
    rng: random.Random,
    data: Dataset,
    sim: ItemSim,
    first_seq: int,
    read_seqs: list[tuple[uuid.UUID, uuid.UUID, int, datetime]],
) -> None:
    """item_reads: the newest event each person had seen when they last opened the item.

    Anything after it is "updated since you looked". Events are identified by their emission
    sequence here; _items() swaps in their final position once all events are sorted.
    """
    readers = [(sim.requester, 0.8)]
    if sim.assignee is not None and sim.assignee != sim.requester:
        readers.append((sim.assignee, 0.9))
    for uid, probability in readers:
        if rng.random() > probability:
            continue
        behind = 0 if rng.random() < 0.6 else rng.randint(1, 3)
        index = max(0, len(sim.events) - 1 - behind)
        read_at = min(sim.events[index].at + timedelta(seconds=5), data.now)
        read_seqs.append((uid, sim.id, first_seq + index, read_at))
