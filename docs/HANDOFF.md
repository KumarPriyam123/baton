# Handoff

Where the project stands, how to run it, and what to know before the next session.

## Current state

**Phase 2 (identity, sessions, authorization, visibility) is done. Phase 3 has not started.** Tags: `phase-2-start`, `phase-2-done`.

Repo: https://github.com/KumarPriyam123/baton (private). CI is green on `main` (api, web, hygiene).

What exists, on top of phases 0 and 1 (stack, schema, seed):

- **Errors** (`app/domain/errors.py`, `app/api/errors.py`, `app/api/problem.py`): one class per SPEC 12 row, problem+json with `code` and `request_id`; validation is 400 and never echoes input; router 404/405 and crashes use the same shape.
- **Engine and readiness:** async engine in the app lifespan (`app/db/engine.py`); `/healthz` is liveness, `/readyz` checks the database and the migration head.
- **Authorization** (`app/domain/policy.py`, pure): `can()` covers every row of SPEC 5.2, `can_view()` and `visibility_clause()` are the two forms of SPEC 5.1. Invisible item gives code `NOT_FOUND`; visible but forbidden gives `FORBIDDEN` plus a reason.
- **Auth** (`app/auth`, `app/services/auth.py`, `app/api/csrf.py`): argon2id, Postgres sessions (hash only, 12 h idle expiry), login throttling (5 failures, 15 min), CSRF double-submit on every unsafe method, `POST /auth/login`, `POST /auth/logout`, `GET /me`, `GET /demo/users` (demo mode only).
- **Teams and people:** `GET /teams`, `GET /teams/{key}/members`, `POST|PATCH|DELETE /teams/{key}/members[/{user_id}]`, `GET /users?q=`.
- **Tests:** 2,853 API (unit, db, integration) and 4 web; all need real Postgres and none skip. `tests/support/` has scratch databases, a seeded-database helper and a "run the real app" helper.

Verified in this phase: every Done-when item at policy and SQL level (see `docs/REQUIREMENTS_TRACE.md`), CI green, the whole auth flow through nginx with curl.
**Still deferred:** `docker compose up` on port 8080 itself (another local project holds it); re-check at the phase 14 clean-clone run.

Not built yet, by design: `record_event`, `command_tx`, idempotency, any item endpoint, workflow, the worker, SSE, the web UI.

## Commands

| Task | Command |
|---|---|
| Start everything | `docker compose up --build` (http://localhost:8080; if taken, `WEB_PORT=8081` in `.env`) |
| API tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` (about 70 s) |
| One test file | `... run --rm api-test pytest tests/integration/test_csrf.py -q` |
| Web unit tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test` |
| After dependency or Dockerfile changes | add `--build`: `run --rm --build api-test` |
| Re-seed demo from scratch | `docker compose run --rm seed python -m scripts.seed --size demo --reset` |
| Large seed (about 70 s) | `docker compose run --rm seed python -m scripts.seed --size large --reset` |
| Replay every history | `docker compose run --rm seed python -m scripts.verify_seed` |
| New migration | `docker compose run --rm migrate alembic revision -m "<msg>"` |
| Tear down (also stops `db-test`) | `docker compose -f compose.yaml -f compose.test.yaml down --remove-orphans` |
| API checks on the host | `cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests scripts` |
| Secret scan | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" zricethezav/gitleaks:v8.30.1 detect --source /repo --no-banner` |

Demo accounts (all `@baton.test`, password `baton-demo`, from `GET /api/v1/demo/users`): `priya.lead` (Payments lead), `asha` and `rahul` (Payments members), `meera` (Support member, Payments viewer), `dev.viewer` (viewer on Payments and SRE), `farah` (Compliance member: cannot see others' confidential items), `admin`. Two leads per team.

Trying the API by hand: sign in with a cookie jar, then send the `baton_csrf` cookie value in an `X-CSRF-Token` header on every POST/PUT/PATCH/DELETE (even logout):

```bash
curl -c jar -X POST localhost:8080/api/v1/auth/login -H 'content-type: application/json' \
     -d '{"email":"priya.lead@baton.test","password":"baton-demo"}'
curl -b jar localhost:8080/api/v1/me
```

## Hazards

- **Port 8080** is held by `infra-latex-1` (an unrelated project). Do not stop it. Use `WEB_PORT`.
- **`compose run` never rebuilds.** `api-test` and `web-test` mount the source; dependency or Dockerfile changes need `--build`.
- **Never edit an applied migration.** Add `0004`... (head is `0003`; `/readyz` and a test pin the head).
- **Warnings are errors in pytest.** A coroutine you forget to await shows up as a failure in some *other* test. If an unrelated test suddenly fails, look for a recent un-awaited call.
- **Middleware order matters** (`app/main.py`): request id outermost, then CSRF, then routing. CSRF runs before authentication and before 404/405, so a bare `curl -X POST` gets 403 `CSRF_FAILED`, not 401 or 404.
- **Time in SQL:** auth code passes `now` in from Python (never `now()` in SQL) so tests can move the clock with `time-machine`. Keep it that way.
- **asyncpg parameter types:** a bind parameter used only inside a `CASE` is inferred as `text`; cast it (`CAST(:x AS timestamptz)`).
- **The seed writes `work_items` and `item_events` directly** (ENGINEERING_DECISIONS 7). Application code must use `record_event()` (phase 3).
- **`seed --reset` TRUNCATEs everything,** including `item_events` (the append-only trigger does not cover TRUNCATE; see `docs/KNOWN_LIMITATIONS.md`).
- **Git Bash:** `docker run -v` paths get mangled (`MSYS_NO_PATHCONV=1`, `pwd -W`); `/tmp` is not the same place for bash and Windows Python (use repo-relative temp files); big heredocs with quotes can fail to parse (write files with the Write tool); scripted edits must `assert` that a replacement matched.
- **ESLint is pinned to 9** until `eslint-plugin-jsx-a11y` supports 10.
- **Git identity** in this repo is `kpriyam2005p@gmail.com`; the account email is `k2005priyam@gmail.com`. Unconfirmed which is intended.

## Decisions that shape later phases

Read `docs/ENGINEERING_DECISIONS.md` 8-22 before phase 3. In short:

- `version` bumps only for decision-relevant changes; comments, `sla_breached` and `duplicate_suggested` write an event with the **current** version (8). `changes_since` lists versioned events; the timeline cursor is `after_event_id`; the client guard compares `(version, last_event_id)`. Unread is `item_reads.last_read_event_id` (12).
- Every event of a command carries the item's team after the command (9).
- Indexes are exactly the SPEC list; swap in expression indexes only if `EXPLAIN` shows a sort for `COALESCE(due_at, 'infinity')` (10).
- Membership removal and demotion are refused while the person owns open items (14). CSRF is checked before routing (18). Any signed-in user can list members (17, SPEC A13). No `POST /teams` yet (20).

## Open items for the next phases

**Phase 3 (work items core)**
- **HTTP-level confidentiality test** (added to the Phase 3 "Done when"): `GET /items/{key}` returns **404 (not 403)** to a non-lead member of a Compliance team for a confidential item, and the item is absent from `GET /items`, facets and search. Phase 2 could only prove it at policy and SQL level. (Search arrives in phase 5 and joins the same test then.)
- Build `command_tx()` and move the membership commands (and login's writes) onto it. Today they commit directly and rely on the engine's 10 s `statement_timeout`; they have no `lock_timeout` and no 40001/40P01 retry (CLAUDE.md I5).
- Every item query uses `visibility_clause(ctx)` from `app/domain/policy.py`; item routes use `can()` and turn `NOT_FOUND` into 404, `FORBIDDEN` into 403 with the reason.
- `GET /items/{key}` needs `ItemFacts` (team, requester, assignee, confidential, status, team name, pending approval's requester).
- One deliberate exception to I3 exists: `count_open_items_owned` in `app/repo/teams.py` counts without the visibility clause (a hidden item must still block a removal). It is safe because only leads of that team and admins reach it, and a test proves they see every item of the team. Do not copy the pattern.

**Phase 4 (workflow, ownership, approvals)**
- **Replace the membership refusal with the automatic unassign** of SPEC 4.3b: in `app/services/teams.py`, `_refuse_if_owns_open_items` becomes "unassign their open items in the same transaction, `unassigned` event with reason 'Removed from <team>', items go to `new`; a pending approval is cancelled first" (ENGINEERING_DECISIONS 14). Also covers account deactivation (no endpoint yet). The tests in `tests/integration/test_teams_members.py` that expect 409 change to expect the unassign. (This TODO was only in the decisions file and a docstring before this close-out; it is now in the Phase 4 "Done when" too.)
- `domain/workflow.py` decides state validity; `policy.can()` stays about people and relationships.
- Add the SPEC 13.1 test that replays a seeded sample through `workflow.py`.

**Phase 5**
- T-VIS extended: search, facets, attention, stats, decisions, similar and notifications never return or count an item the caller cannot view.

## Next phase

Phase 3: work items core (SPEC 6.2-6.4, 8, and the item endpoints of SPEC 11 except workflow commands): `command_tx`, `record_event`, idempotency, create/read/edit/list with keyset pagination and facets.
