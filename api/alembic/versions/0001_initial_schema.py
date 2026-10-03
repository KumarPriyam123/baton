"""Initial schema: SPEC section 3, tables, constraints, indexes and the append-only trigger.

Revision ID: 0001
Revises:

Written as plain SQL on purpose: enums, the generated tsvector, partial indexes, CHECKs and the
trigger are clearer in SQL than in a builder. Every constraint is named so the API layer can map
a violation to a domain error (app/db/errors.py, phase 3). One statement per op.execute, because
the asyncpg driver prepares a single statement at a time.

Indexes are exactly the ones SPEC 3.2 names. Indexes for queries that do not exist yet are added
with those queries, in later migrations.
"""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

EVENT_KINDS = (
    "created",
    "field_changed",
    "priority_changed",
    "assigned",
    "unassigned",
    "status_changed",
    "blocked",
    "unblocked",
    "resolved",
    "reopened",
    "closed",
    "transferred",
    "approval_requested",
    "approval_approved",
    "approval_rejected",
    "approval_cancelled",
    "approval_invalidated",
    "requires_approval_changed",
    "confidential_changed",
    "commented",
    "sla_breached",
    "duplicate_suggested",
)

ENUMS = {
    "team_role": ("viewer", "member", "lead"),
    "item_type": (
        "incident",
        "customer_issue",
        "payment_investigation",
        "engineering",
        "compliance_request",
        "ops_task",
    ),
    "item_status": ("new", "in_progress", "blocked", "awaiting_approval", "resolved", "closed"),
    "resolution": ("done", "duplicate", "wont_do", "cannot_reproduce"),
    "approval_status": ("pending", "approved", "rejected", "cancelled", "invalidated"),
    "due_source": ("auto", "manual"),
    "outbox_status": ("pending", "done", "dead"),
}

# Dropped in reverse order on downgrade.
TABLES = (
    "users",
    "teams",
    "memberships",
    "work_items",
    "item_events",
    "comments",
    "approvals",
    "watchers",
    "item_reads",
    "notifications",
    "item_similar",
    "idempotency_keys",
    "outbox",
    "sessions",
)

CREATE_TABLES = [
    """
    CREATE TABLE users (
        id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        email          citext NOT NULL,
        name           text NOT NULL,
        password_hash  text NOT NULL,
        is_admin       boolean NOT NULL DEFAULT false,
        failed_logins  integer NOT NULL DEFAULT 0,
        locked_until   timestamptz,
        created_at     timestamptz NOT NULL DEFAULT now(),
        deactivated_at timestamptz,
        CONSTRAINT users_email_key UNIQUE (email)
    )
    """,
    """
    CREATE TABLE teams (
        id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        key         text NOT NULL,
        name        text NOT NULL,
        description text NOT NULL DEFAULT '',
        item_seq    integer NOT NULL DEFAULT 0,
        created_at  timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT teams_key_key UNIQUE (key),
        CONSTRAINT teams_key_format CHECK (key ~ '^[A-Z]{2,5}$')
    )
    """,
    """
    CREATE TABLE memberships (
        team_id    uuid NOT NULL REFERENCES teams (id),
        user_id    uuid NOT NULL REFERENCES users (id),
        role       team_role NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT memberships_pkey PRIMARY KEY (team_id, user_id)
    )
    """,
    """
    CREATE TABLE work_items (
        id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        key               text NOT NULL,
        team_id           uuid NOT NULL REFERENCES teams (id),
        number            integer NOT NULL,
        origin_team_id    uuid NOT NULL REFERENCES teams (id),
        type              item_type NOT NULL,
        title             text NOT NULL,
        description       text NOT NULL DEFAULT '',
        priority          smallint NOT NULL,
        status            item_status NOT NULL DEFAULT 'new',
        resolution        resolution,
        resolution_note   text,
        duplicate_of_id   uuid REFERENCES work_items (id),
        requester_id      uuid NOT NULL REFERENCES users (id),
        assignee_id       uuid REFERENCES users (id),
        confidential      boolean NOT NULL,
        requires_approval boolean NOT NULL,
        due_at            timestamptz,
        due_source        due_source NOT NULL DEFAULT 'auto',
        sla_breached_at   timestamptz,
        last_event_id     bigint,
        last_activity_at  timestamptz NOT NULL DEFAULT now(),
        version           integer NOT NULL DEFAULT 1,
        created_at        timestamptz NOT NULL DEFAULT now(),
        updated_at        timestamptz NOT NULL DEFAULT now(),
        resolved_at       timestamptz,
        closed_at         timestamptz,
        search            tsvector GENERATED ALWAYS AS (
            setweight(to_tsvector('simple'::regconfig, coalesce(key, '')), 'A') ||
            setweight(to_tsvector('english'::regconfig, title), 'A') ||
            setweight(to_tsvector('english'::regconfig, coalesce(description, '')), 'B')
        ) STORED,
        CONSTRAINT work_items_key_key UNIQUE (key),
        CONSTRAINT work_items_origin_number_key UNIQUE (origin_team_id, number),
        CONSTRAINT work_items_title_length CHECK (char_length(title) BETWEEN 3 AND 200),
        CONSTRAINT work_items_description_length CHECK (char_length(description) <= 20000),
        CONSTRAINT work_items_priority_range CHECK (priority BETWEEN 0 AND 3),
        CONSTRAINT owner_when_active CHECK (
            status NOT IN ('in_progress', 'blocked', 'awaiting_approval')
            OR assignee_id IS NOT NULL
        ),
        CONSTRAINT resolution_when_done CHECK (
            status NOT IN ('resolved', 'closed') OR resolution IS NOT NULL
        ),
        CONSTRAINT duplicate_has_target CHECK (
            resolution IS DISTINCT FROM 'duplicate' OR duplicate_of_id IS NOT NULL
        ),
        CONSTRAINT not_self_duplicate CHECK (duplicate_of_id IS DISTINCT FROM id)
    )
    """,
    """
    CREATE TABLE item_events (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        item_id      uuid NOT NULL REFERENCES work_items (id),
        team_id      uuid NOT NULL REFERENCES teams (id),
        actor_id     uuid REFERENCES users (id),
        kind         text NOT NULL,
        item_version integer NOT NULL,
        data         jsonb NOT NULL DEFAULT '{}'::jsonb,
        reason       text,
        is_decision  boolean NOT NULL DEFAULT false,
        request_id   text,
        created_at   timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT item_events_kind_valid CHECK (kind IN (__KINDS__))
    )
    """.replace("__KINDS__", ", ".join(f"'{k}'" for k in EVENT_KINDS)),
    """
    CREATE TABLE comments (
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        item_id    uuid NOT NULL REFERENCES work_items (id),
        author_id  uuid NOT NULL REFERENCES users (id),
        body       text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT comments_body_length CHECK (char_length(body) BETWEEN 1 AND 10000)
    )
    """,
    """
    CREATE TABLE approvals (
        id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
        item_id            uuid NOT NULL REFERENCES work_items (id),
        status             approval_status NOT NULL DEFAULT 'pending',
        requested_by       uuid NOT NULL REFERENCES users (id),
        requested_at       timestamptz NOT NULL DEFAULT now(),
        request_note       text,
        subject_hash       text NOT NULL,
        decided_by         uuid REFERENCES users (id),
        decided_at         timestamptz,
        decision_note      text,
        invalidated_reason text,
        CONSTRAINT approvals_four_eyes CHECK (decided_by IS DISTINCT FROM requested_by),
        CONSTRAINT approvals_rejection_has_note CHECK (
            status <> 'rejected' OR nullif(btrim(decision_note), '') IS NOT NULL
        )
    )
    """,
    """
    CREATE TABLE watchers (
        item_id    uuid NOT NULL REFERENCES work_items (id),
        user_id    uuid NOT NULL REFERENCES users (id),
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT watchers_pkey PRIMARY KEY (item_id, user_id)
    )
    """,
    """
    CREATE TABLE item_reads (
        user_id           uuid NOT NULL REFERENCES users (id),
        item_id           uuid NOT NULL REFERENCES work_items (id),
        last_read_version integer NOT NULL,
        read_at           timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT item_reads_pkey PRIMARY KEY (user_id, item_id)
    )
    """,
    """
    CREATE TABLE notifications (
        id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        user_id    uuid NOT NULL REFERENCES users (id),
        item_id    uuid NOT NULL REFERENCES work_items (id),
        event_id   bigint NOT NULL REFERENCES item_events (id),
        kind       text NOT NULL,
        created_at timestamptz NOT NULL DEFAULT now(),
        read_at    timestamptz,
        CONSTRAINT notifications_user_event_key UNIQUE (user_id, event_id)
    )
    """,
    """
    CREATE TABLE item_similar (
        item_id         uuid NOT NULL REFERENCES work_items (id),
        similar_item_id uuid NOT NULL REFERENCES work_items (id),
        score           real NOT NULL,
        created_at      timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT item_similar_pkey PRIMARY KEY (item_id, similar_item_id)
    )
    """,
    """
    CREATE TABLE idempotency_keys (
        user_id         uuid NOT NULL REFERENCES users (id),
        key             text NOT NULL,
        fingerprint     text NOT NULL,
        response_status integer,
        response_body   jsonb,
        created_at      timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT idempotency_keys_pkey PRIMARY KEY (user_id, key)
    )
    """,
    """
    CREATE TABLE outbox (
        id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        topic        text NOT NULL,
        payload      jsonb NOT NULL DEFAULT '{}'::jsonb,
        dedupe_key   text,
        status       outbox_status NOT NULL DEFAULT 'pending',
        attempts     integer NOT NULL DEFAULT 0,
        available_at timestamptz NOT NULL DEFAULT now(),
        locked_until timestamptz,
        last_error   text,
        created_at   timestamptz NOT NULL DEFAULT now(),
        processed_at timestamptz,
        CONSTRAINT outbox_dedupe_key_key UNIQUE (dedupe_key)
    )
    """,
    """
    CREATE TABLE sessions (
        id           text PRIMARY KEY,
        user_id      uuid NOT NULL REFERENCES users (id),
        created_at   timestamptz NOT NULL DEFAULT now(),
        expires_at   timestamptz NOT NULL,
        last_seen_at timestamptz NOT NULL DEFAULT now(),
        user_agent   text
    )
    """,
]

# Each index exists for a named query (SPEC 3.2).
CREATE_INDEXES = [
    "CREATE INDEX memberships_user_id_idx ON memberships (user_id)",
    # team queue, default sort
    "CREATE INDEX work_items_team_queue_idx ON work_items (team_id, status, priority, due_at, id)",
    # "recently updated" sort
    "CREATE INDEX work_items_team_updated_idx ON work_items (team_id, updated_at DESC, id DESC)",
    # "assigned to me"
    """CREATE INDEX work_items_assignee_open_idx ON work_items (assignee_id, priority, due_at)
       WHERE status NOT IN ('resolved', 'closed')""",
    # "needs an owner"
    """CREATE INDEX work_items_needs_owner_idx ON work_items (team_id, priority, created_at)
       WHERE assignee_id IS NULL AND status = 'new'""",
    # "my requests"
    "CREATE INDEX work_items_requester_idx ON work_items (requester_id, updated_at DESC)",
    # SLA sweep
    """CREATE INDEX work_items_sla_sweep_idx ON work_items (due_at)
       WHERE status NOT IN ('resolved', 'closed') AND sla_breached_at IS NULL""",
    # going stale
    """CREATE INDEX work_items_going_stale_idx ON work_items (team_id, last_activity_at)
       WHERE status IN ('in_progress', 'blocked')""",
    # full-text search
    "CREATE INDEX work_items_search_idx ON work_items USING gin (search)",
    # fuzzy title search and duplicate suggestions
    "CREATE INDEX work_items_title_trgm_idx ON work_items USING gin (title gin_trgm_ops)",
    "CREATE INDEX item_events_item_idx ON item_events (item_id, id DESC)",
    "CREATE INDEX item_events_decisions_idx ON item_events (team_id, id DESC) WHERE is_decision",
    "CREATE INDEX item_events_created_brin ON item_events USING brin (created_at)",
    # one open approval request per item; the name maps to APPROVAL_ALREADY_PENDING
    "CREATE UNIQUE INDEX approvals_one_pending ON approvals (item_id) WHERE status = 'pending'",
    """CREATE INDEX notifications_unread_idx ON notifications (user_id, id DESC)
       WHERE read_at IS NULL""",
    "CREATE INDEX idempotency_keys_created_idx ON idempotency_keys (created_at)",
    "CREATE INDEX outbox_pending_idx ON outbox (available_at, id) WHERE status = 'pending'",
]

APPEND_ONLY = [
    """
    CREATE FUNCTION reject_item_events_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'item_events is append-only: % is not allowed', TG_OP
            USING ERRCODE = 'restrict_violation';
    END;
    $$
    """,
    """
    CREATE TRIGGER item_events_append_only
        BEFORE UPDATE OR DELETE ON item_events
        FOR EACH ROW EXECUTE FUNCTION reject_item_events_mutation()
    """,
]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    for name, values in ENUMS.items():
        labels = ", ".join(f"'{v}'" for v in values)
        op.execute(f"CREATE TYPE {name} AS ENUM ({labels})")
    for statement in CREATE_TABLES:
        op.execute(statement)
    for statement in CREATE_INDEXES:
        op.execute(statement)
    for statement in APPEND_ONLY:
        op.execute(statement)


def downgrade() -> None:
    # The trigger blocks DELETE, not DROP. Dropping the table removes the trigger with it.
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_item_events_mutation()")
    for name in reversed(list(ENUMS)):
        op.execute(f"DROP TYPE IF EXISTS {name}")
    op.execute("DROP EXTENSION IF EXISTS pg_trgm")
    op.execute("DROP EXTENSION IF EXISTS citext")
