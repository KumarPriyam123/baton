# Handoff

Where the project stands, how to run it, and what to know before the next session.

## Current state

**Phase 1 (schema, migrations, seed data) is done. Phase 2 has not started.** Tags: `phase-1-start`, `phase-1-done`.

Repo: https://github.com/KumarPriyam123/baton (private). CI is green on `main` (api, web, hygiene).

What exists, on top of phase 0 (healthz, request ids, JSON logs, Docker, CI):

- **Schema** (`api/alembic/versions`): `0001` all 14 SPEC 3 tables, 7 enums, named constraints, the generated `search` column, the SPEC indexes and the `item_events` append-only trigger; `0002` `item_reads.last_read_event_id`; `0003` identity-immutability triggers (`teams.key`, `work_items.key/number/origin_team_id`). Hand-written SQL, one statement per `op.execute`.
- **Python mirrors:** `app/db/schema.py` (SQLAlchemy Core tables), `app/domain/enums.py`, `app/domain/hashing.py` (`subject_hash`), `app/db/urls.py`. Drift tests fail if they diverge from the migrated database.
- **Seed** (`api/scripts/seed.py`, `scripts/seedlib/`): deterministic `demo` (24 personas, 6 teams, 600 items, about 4,700 events) and `large` (2,000 users, 40 teams, 50,000 items of which 18,107 open, 586,983 events, 127,714 comments). `ItemSim` walks the SPEC 4.1 table; `verify.py` replays events from the database. `--reset`, `--seed`, `--dry-run`, `--verify`. `python -m scripts.verify_seed` checks every history.
- **Compose:** `migrate` runs `alembic upgrade head` (with `api/alembic` mounted, as root, so `alembic revision` writes to the host); `seed` loads the demo data when the database is empty.
- **Tests:** 249 API (unit, db, integration) and 4 web; all need real Postgres and none skip.

Verified in this phase: every Done-when item (see `docs/REQUIREMENTS_TRACE.md`), CI green, large seed and all 50,000 histories replayed.
**Still deferred:** `docker compose up` on port 8080 itself (another local project holds it); re-check at the phase 14 clean-clone run.

Not built yet, by design: engine/lifespan, `/readyz` (phase 2), auth, CSRF, `record_event`, any item endpoint, the worker, SSE.

## Commands

| Task | Command |
|---|---|
| Start everything | `docker compose up --build` (http://localhost:8080; if taken, `WEB_PORT=8081` in `.env`) |
| API tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` |
| Web unit tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test` |
| After dependency or Dockerfile changes | add `--build`: `run --rm --build api-test` |
| Re-seed demo from scratch | `docker compose run --rm seed python -m scripts.seed --size demo --reset` |
| Large seed (about 70 s) | `docker compose run --rm seed python -m scripts.seed --size large --reset` |
| Size check without a database | `docker compose run --rm seed python -m scripts.seed --size large --dry-run` |
| Replay every history | `docker compose run --rm seed python -m scripts.verify_seed` |
| New migration | `docker compose run --rm migrate alembic revision -m "<msg>"` (file appears in `api/alembic/versions`) |
| Tear down (also stops `db-test`) | `docker compose -f compose.yaml -f compose.test.yaml down --remove-orphans` |
| API checks on the host | `cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests scripts` |
| Secret scan | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" zricethezav/gitleaks:v8.30.1 detect --source /repo --no-banner` |

Demo accounts (all `@baton.test`, password `baton-demo`): `priya.lead` (Payments lead), `asha` and `rahul` (Payments members), `meera` (Support member), `dev.viewer` (viewer on Payments and SRE), `farah` (Compliance member, cannot see others' confidential items), `admin`. Two leads per team.

## Hazards

- **Port 8080** is held by `infra-latex-1` (an unrelated project). Do not stop it. Use `WEB_PORT`.
- **`compose run` never rebuilds.** `api-test` and `web-test` mount the source; dependency or Dockerfile changes need `--build`.
- **Never edit an applied migration.** Add `0004`... The tests rebuild the schema from the migrations, so a changed old migration would hide a real upgrade problem.
- **Seed history is data, not truth:** `seed --reset` TRUNCATEs everything, including `item_events` (the append-only trigger does not cover TRUNCATE; see `docs/KNOWN_LIMITATIONS.md`).
- **The seed writes `work_items` and `item_events` directly** (ENGINEERING_DECISIONS #7). Application code must never do that: use `record_event()` (phase 3).
- **asyncpg COPY cannot encode `citext`:** users are inserted with `$2::text::citext`.
- **`alembic/env.py` calls `configure_logging`**, which replaces root log handlers; harmless in tests, but do not rely on `caplog` after running migrations.
- **Git Bash:** `docker run -v` paths get mangled (`MSYS_NO_PATHCONV=1`, `pwd -W`); big heredocs with quotes can fail to parse (write files with the Write tool). Python patch scripts must `assert` that a replacement matched; two of mine silently did nothing.
- **ESLint is pinned to 9** until `eslint-plugin-jsx-a11y` supports 10.
- **Git identity** in this repo is `kpriyam2005p@gmail.com`; the account email is `k2005priyam@gmail.com`. Unconfirmed which is intended.

## Decisions that shape phase 3 onward

Read `docs/ENGINEERING_DECISIONS.md` 6-13 before writing `record_event()`. In short:

- `version` bumps only for decision-relevant changes; comments, `sla_breached` and `duplicate_suggested` write an event with the **current** version (SPEC 6.2). `changes_since` lists versioned events; the timeline cursor is `after_event_id`; the client guard compares `(version, last_event_id)`.
- Unread is `item_reads.last_read_event_id`; the item DTO has `unread_since_event_id` and `last_event_id`.
- Every event of a command carries the item's team after the command.
- Indexes are exactly the SPEC list. If `EXPLAIN` shows a sort for `COALESCE(due_at, 'infinity')` keyset pages, swap in expression indexes with a new migration (decision 10).
- No event kind exists for watch changes, so they write no event.

## Next phase

Phase 2: identity, sessions, authorization, visibility (SPEC 5).

- Introduce the async engine and app lifespan; add `/readyz` (DB reachable, migrations at head) here.
- `app/auth` (argon2 hashes already exist in the seed: `PasswordHasher()` defaults), sessions in Postgres, CSRF double-submit, login throttling.
- `domain/policy.py` and `visibility_clause()`; the property test T-VIS compares them on the seeded data. The demo seed already contains the cases it needs (confidential Compliance items, a viewer on two teams, a member who cannot see others' confidential items, an admin outside every team).
- `GET /demo/users` only when `DEMO_MODE=true`.
