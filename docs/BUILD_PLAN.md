# Baton — Build Plan for Claude Code

Fifteen phases. Each phase is one Claude Code session (some take two). Run them in order; never start the next phase until the current one's **Done when** list is fully green.

**Files Claude Code works from**

| File | Role |
|---|---|
| `CLAUDE.md` | rules that apply in every session (stack, invariants, conventions) |
| `docs/SPEC.md` | what the system does — the source of truth |
| `docs/DESIGN.md` | how it looks and speaks |
| `docs/BUILD_PLAN.md` | this file: order, prompts, definitions of done |
| `docs/REQUIREMENTS_TRACE.md` | every line of the brief → where it's built → what proves it |

## Phase map

Must = needed for a complete submission. Should = makes it stand out. If time ever runs short, the Must phases alone are a complete, coherent submission.

| # | Phase | Priority | Brief areas it covers | Read it yourself |
|---|---|---|---|---|
| 0 | Foundation: repo, Docker, CI, tooling | Must | maintainability, run instructions | `compose.yaml` |
| 1 | Schema, migrations, seed data | Must | data modelling, scale | the migration file |
| 2 | Identity, sessions, authorization, visibility | Must | teams, identity, access | `domain/policy.py` |
| 3 | Work items core: create, read, edit, list, history, idempotency | Must | API design, consistency, duplicates, stale info | `api/idempotency.py`, `db/tx.py` |
| 4 | Workflow, ownership, approvals | Must | workflow rules, concurrent claims, approvals | `domain/workflow.py`, `services/commands.py` |
| 5 | Collaboration, attention, search, dashboard API | Must | collaboration, overview, search & filtering | `repo/attention.py`, `repo/search.py` |
| 6 | Async worker: outbox, notifications, SLA, duplicates | Must | async processing, failure handling | `worker/runner.py` |
| 7 | Live updates (SSE) | Should | stale views, concurrency UX | `realtime/hub.py` |
| 8 | Frontend foundation and design system | Must | frontend state, UX | `lib/api.ts`, `styles/tokens.css` |
| 9 | Inbox, queue, search, new request | Must | overview, attention, search | `lib/useCommand.ts`, `lib/itemCache.ts` |
| 10 | Item detail, actions, approvals, conflicts | Must | optimistic reconciliation, stale info | `features/item/ConflictDialog.tsx` |
| 11 | Dashboard, decisions, notifications, team admin, jobs | Should | management overview, authorization UX | — |
| 12 | Scale and performance proof | Should | performance, scale | `docs/PERFORMANCE.md` |
| 13 | End-to-end tests, sabotage checks, hardening | Must | testing, error handling, security | `docs/TESTING.md` |
| 14 | Documentation, demo script, release | Must | submission items, final discussion | all of `docs/` |

Phase 10's live-update parts depend on phase 7. If phase 7 is ever skipped, the open item polls every 10 s instead, and the version checks still catch every stale write.

"Read it yourself" is the file **you** should read line by line after the phase. The brief says you may be asked to explain or change any part live.

---

## Every session: opener

Paste this at the start of each phase, replacing N:

```text
We're building Baton, phase N.
Read CLAUDE.md, docs/SPEC.md, and "Phase N" in docs/BUILD_PLAN.md. For frontend phases also read docs/DESIGN.md.

Before writing code:
1. Restate the phase goal and its "Done when" list in your own words.
2. List the files you expect to create or change.
3. List anything in the spec that is ambiguous or looks wrong. If any of it blocks you, stop and ask me. Do not quietly pick an interpretation that contradicts the spec.

Then build in small, real commits (conventional messages). Run the relevant tests after each step.
Stay inside phase N. Do not start phase N+1.
```

## Every session: close-out

Paste this when Claude Code says the phase is done:

```text
Phase N close-out:
1. Run the full test suite for api and web. Paste the summary lines. A skipped test counts as a failure.
2. Go through "Done when" one item at a time: ✅ with evidence (test name, command output, screenshot path) or ❌ with what's missing. If you couldn't verify something (tool unavailable, browser not connected), write "unverified" — never tick it.
3. Check your diff for this phase against the invariants in CLAUDE.md (I1–I14). List any violation, then fix it.
4. Update docs/REQUIREMENTS_TRACE.md (status + proof), docs/HANDOFF.md (current state, commands, hazards, next phase), and add each problem you hit to docs/NOTES_FOR_INTERVIEW.md as: problem → root cause → fix → lesson.
5. Add to NOTES_FOR_INTERVIEW.md a 10-line "explain this phase" section for me: what was built, the key decision and its alternative, what breaks if the main guard is removed, and the file:line where that guard lives.
6. Commit and push.
```

## After Must phases 4, 6, 10 and 13: independent review

```text
Use a subagent that hasn't seen this session. Give it CLAUDE.md, docs/SPEC.md and the output of `git diff <tag-before-phase>..HEAD`.
Ask it to find: invariant violations, race conditions, authorization gaps, missing tests for a Done-when item, tests that would still pass if their guard were removed, and places where code and SPEC disagree.
Have it report findings ranked by severity with file:line. Verify each finding yourself, fix the confirmed ones, and list the rejected ones with the reason.
```

Tag the start of every phase: `git tag phase-N-start`.

## When something is broken and Claude Code is going in circles

```text
Stop editing code. Reproduce the failure with the smallest possible command and show the output.
Give three hypotheses, ranked by likelihood. Test the cheapest one first and show the evidence.
Only then fix it, and add a test that fails without the fix.
```

---

## Phase 0 — Foundation

**Goal:** an empty but real project: one command starts everything, tests run in containers, CI is green, and Windows can't break the Linux containers.

```text
Set up the Baton repository in this folder per CLAUDE.md "Stack" and "Repository layout".

Repository hygiene
- git init on main. .gitignore covers .env, *.pdf (the brief stays out of the public repo), node_modules, dist, .venv, __pycache__, .pytest_cache, playwright-report, test-results, coverage.
- .gitattributes: `* text=auto eol=lf` (scripts and Dockerfiles must have LF endings inside Linux containers even though I'm on Windows).
- .editorconfig; pre-commit with ruff, ruff-format and gitleaks.
- .env.example listing every config variable with a safe dev default and a comment.

api/ (Python 3.12, uv)
- pyproject.toml with the dependencies in CLAUDE.md. ruff (strict-ish rule set) and mypy (strict for app/domain and app/services).
- app/main.py: FastAPI app, routers mounted under /api/v1, GET /api/v1/healthz → {"status":"ok"}.
- app/config.py: pydantic-settings (ENV dev|test|prod, DATABASE_URL, TEST_DATABASE_URL, DEMO_MODE, SESSION_TTL_HOURS, FAULT_NOTIFY_FAIL_RATE). In ENV=prod, refuse to start if DEMO_MODE or any FAULT_* is set.
- Structured JSON logging (structlog) with a request-id middleware: read X-Request-ID or generate one, put it in the log context and the response header.
- tests/conftest.py: if TEST_DATABASE_URL is missing, FAIL the session with a clear message. Never skip database tests.

web/ (Vite + React + TypeScript, npm)
- Tailwind v4, ESLint (typescript-eslint, react-hooks, jsx-a11y), Prettier, Vitest.
- One placeholder page that calls /api/v1/healthz and shows the result.

Docker
- compose.yaml services: db (postgres:16 with healthcheck), migrate (one-shot; placeholder until phase 1), api (uvicorn), worker (placeholder loop that logs a heartbeat), web (multi-stage: build the SPA, serve with nginx; proxy /api to api; for /api/v1/stream set proxy_buffering off, proxy_read_timeout 1h).
- Only web is published, on port 8080.
- compose.test.yaml: a test database on tmpfs, `api-test` (runs pytest), `web-test` (runs vitest).
- README stub with the two commands: `docker compose up --build` and the test command.

CI (.github/workflows/ci.yml)
- api job: ruff, mypy, pytest against a postgres service.
- web job: eslint, tsc --noEmit, vitest, vite build.

Docs
- Create docs/HANDOFF.md, docs/NOTES_FOR_INTERVIEW.md, docs/TESTING.md (empty templates with headings).
```

**Done when**
- [ ] From a fresh clone, `docker compose up --build` serves the placeholder at http://localhost:8080 and `/api/v1/healthz` returns `ok` **through nginx**
- [ ] `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` passes one trivial test
- [ ] With `TEST_DATABASE_URL` unset, pytest **fails** with the clear message (it doesn't skip)
- [ ] CI is green on GitHub
- [ ] `git ls-files --eol` shows LF for every `.sh`, `Dockerfile` and `.py` file; `gitleaks detect` is clean; the brief PDF and `.env` are not tracked
- [ ] Every response carries `X-Request-ID`, and the same id appears in the API log line

**Failure modes guarded**
- CRLF line endings from Windows break shell entrypoints inside containers (`/bin/sh^M: not found`).
- Database tests skip silently and the suite still looks green (the ClinicQ "130 passed, 16 skipped" trap).
- Secrets or the brief end up in public git history.

---

## Phase 1 — Schema, migrations, seed data

**Goal:** the whole data model from SPEC §3 in Postgres, with the database itself rejecting impossible states, plus realistic demo data.

```text
Implement SPEC §3 as Alembic migrations. Hand-written SQL through op.execute is fine (and clearer) for enums, the generated tsvector column, partial indexes, CHECK constraints and the append-only trigger.

- Extensions: citext, pg_trgm.
- Every table, column, constraint and index exactly as listed in SPEC §3.2. Name constraints explicitly (owner_when_active, resolution_when_done, approvals_one_pending, approvals_four_eyes, …) so errors can be mapped later.
- The `search` generated column must use explicit regconfig casts ('english'::regconfig), otherwise Postgres rejects it as not immutable.
- Trigger on item_events that raises on UPDATE or DELETE.
- app/db/schema.py: SQLAlchemy Core Table objects that mirror the migration (used for queries, not for generating migrations).
- Wire the compose `migrate` service to run `alembic upgrade head`; api and worker depend on it completing successfully.

Seed (api/scripts/seed.py, run as `python -m scripts.seed --size demo|large [--reset]`)
- Idempotent: does nothing if users already exist, unless --reset.
- demo: the 6 teams and named personas in SPEC §13.1 (memberships that make every role and the confidential rule demonstrable), ~600 items with realistic titles per type (e.g. "Refund stuck for order 48213", "p99 latency spike on checkout-api", "KYC re-verification for merchant M-2231"), events, comments, a few pending and approved approvals, some overdue items, some confidential compliance items.
- Histories are generated by walking a transition table that matches SPEC §4.1, so no item has an impossible history. A command that changes a versioned field raises item_version by exactly 1; comments and system events keep the current version (SPEC §6.2).
- large: sizes from SPEC §13.1, inserted with COPY (asyncpg copy_records_to_table), not row-by-row INSERTs.
- A compose `seed` one-shot service runs the demo seed when the database is empty.
```

**Tests** (`tests/db/`)
- One test per constraint in SPEC §3.2 proving it rejects a violating row: owner_when_active, resolution_when_done, duplicate_has_target, not_self_duplicate, one pending approval per item, four-eyes check, append-only trigger (UPDATE and DELETE both raise), unique key, unique `(origin_team_id, number)`.
- Migration round trip: upgrade head → downgrade base → upgrade head on an empty database.
- A test that reads `pg_indexes` and asserts every index named in SPEC §3.2 exists.

**Done when**
- [ ] All constraint tests pass, and each one fails if its constraint is dropped (try one by hand and note it in TESTING.md)
- [ ] Migration round trip is clean
- [ ] Demo seed loads in under 30 s via `docker compose up`; large seed loads in under 5 min
- [ ] Spot-check: 20 random seeded items have valid histories (versions contiguous, statuses consistent with events)

**Failure modes guarded**
- Rules that live only in application code, with nothing in the database to stop a buggy path from writing an impossible row.
- A generated column that fails to migrate (non-immutable expression).
- Seed data with impossible histories, which would make the demo and every later test misleading.

---

## Phase 2 — Identity, sessions, authorization, visibility

**Goal:** real sign-in, and one authorization model enforced on the server for every read and write.

```text
Implement SPEC §5.

Auth
- POST /auth/login, POST /auth/logout, GET /me (user, memberships, csrf token).
- argon2id hashing; 32-byte session tokens; only sha256(token) stored in `sessions`; cookies per SPEC §5.4; 12 h idle expiry with last_seen_at refresh.
- CSRF double-submit middleware for every unsafe method (POST, PUT, PATCH, DELETE), except /auth/login.
- Login throttling: 5 failures → locked 15 min. Same message for unknown email and wrong password.
- GET /demo/users only when DEMO_MODE=true (404 otherwise).

Actor context
- A FastAPI dependency that loads the user and {team_id: role} fresh from the database on every request. Nothing about roles in the cookie.

Policy (app/domain/policy.py — pure, no I/O)
- `can(ctx, action, item_facts) -> Decision(allowed: bool, code: str | None, reason: str | None)` covering every row of SPEC §5.2.
- `can_view(ctx, item_facts)`.
- `visibility_clause(ctx)`: a SQLAlchemy expression implementing SPEC §5.1 for use in WHERE clauses. This is the ONLY way list-type queries may filter by permission.
- Item not visible → 404 NOT_FOUND. Visible but not permitted → 403 FORBIDDEN with the reason.

Errors (app/api/errors.py)
- Domain exception hierarchy → application/problem+json per SPEC §12, always including code and request_id. Unhandled exceptions → 500 INTERNAL with request_id; never leak stack traces.

Teams and users
- GET /teams, GET /teams/{key}/members, membership management per SPEC §5.2 (leads manage members and viewers; only admins manage leads), GET /users?q= (paginated directory).
```

**Tests**
- **Policy matrix:** in the test file, write the expected matrix by hand from SPEC §5.2 (don't import it from the implementation) and check every cell, including confidential items.
- **T-VIS parity:** for every demo user, the set of item ids returned by a query using `visibility_clause` equals the set of items where `can_view` is true, computed over all items.
- CSRF: every unsafe route without the header → 403 `CSRF_FAILED` (iterate over the OpenAPI routes so new routes are covered automatically).
- Sessions: expired session → 401; deactivated user → 401; logout invalidates the token on the server.
- Throttling: the 6th attempt → 429; unknown email gets the same message as a wrong password.
- Removing a membership takes effect on the very next request.

**Done when**
- [ ] Every cell of SPEC §5.2 has a passing test
- [ ] T-VIS parity passes for all demo users
- [ ] A confidential compliance item returns 404 to a non-lead member of its team, and doesn't appear in any list
- [ ] The CSRF test covers every unsafe route found in OpenAPI

**Failure modes guarded**
- Authorization enforced only by hiding buttons in the UI.
- Filtering permissions in Python after `LIMIT`, which breaks pagination and leaks counts.
- 403 responses revealing that a hidden item exists.
- Roles cached in a token, so revoked access keeps working.

---

## Phase 3 — Work items core

**Goal:** create, read, edit and list items with an audit event for every change, optimistic versions, and idempotent writes. This phase builds the transaction machinery every later command reuses.

```text
Implement SPEC §6.2–6.4, §8 (filters, sorts, keyset pagination, facets; not full-text search yet) and the item endpoints in SPEC §11 except workflow commands.

Transaction machinery (app/db/tx.py)
- `command_tx()` context manager implementing SPEC §6.4: SET LOCAL lock_timeout '2s' and statement_timeout '5s'; retry the whole transaction up to 3 times on SQLSTATE 40001/40P01; map lock/statement timeouts to 503 BUSY with Retry-After; map named constraint violations to domain errors.
- Helper `record_event(tx, item, kind, data, reason, is_decision)` that inserts item_events with item_version = the version after the change (the current version for commented, sla_breached and duplicate_suggested, which never bump it), updates work_items.last_event_id (and last_activity_at for events a person wrote), inserts an outbox row (topic 'item.event'), and issues NOTIFY item_changes with the SPEC §10 payload. Every mutation goes through this helper.

Idempotency (app/api/idempotency.py)
- Exactly SPEC §6.3: INSERT … ON CONFLICT DO NOTHING inside the command transaction; SAVEPOINT around the business logic; store 2xx and domain 4xx responses; roll back everything on 5xx; fingerprint mismatch → 422; replay sets `Idempotent-Replayed: true`.
- Required on POST /items; optional elsewhere.

Endpoints
- POST /items: number from teams.item_seq (UPDATE … RETURNING), key = TEAMKEY-number, type defaults (SPEC §3.1), due_at from priority, requester auto-watches, `created` event.
- GET /items/{key}: item DTO per SPEC §11 with ETag "<version>"; allowed_actions computed from policy (workflow actions are added in phase 4); next_step placeholder.
- PATCH /items/{key}: If-Match required (428 if missing); conditional UPDATE on version; 412 VERSION_CONFLICT with `current` and `changes_since` (versioned events with item_version > expected). Rules from SPEC §4.2 (who may edit what, priority-downgrade reason, due recompute, requires_approval off needs lead + reason). Record which material fields changed (used by phase 4).
- GET /items: filters and sorts from SPEC §8, keyset cursor (base64 JSON of sort values + id), limit default 50 max 100, visibility_clause always applied, COALESCE(due_at,'infinity') in both ORDER BY and cursor.
- GET /items/facets, GET /items/{key}/events (cursor; after_event_id).
- Pydantic response models; every route has a stable operation_id (the frontend generates its client from these).
```

**Tests**
- **T-IDEM:** 10 concurrent `POST /items` with one key → exactly 1 item, 10 identical bodies, 9 with `Idempotent-Replayed`. Same key + different body → 422. A domain 4xx is replayed; a forced 5xx is not stored (retry runs again).
- **T-STALE:** two PATCHes on the same version, concurrently → one 200, one 412; final version = old + 1; the 412 body's `changes_since` contains exactly the other change.
- PATCH without If-Match → 428.
- **Event invariant:** each mutation writes exactly one event per change, with contiguous item_version; a failed mutation writes none and no outbox row.
- **Pagination:** with 1,000 items, walking every page returns each visible item exactly once, for every sort, including items with null due_at.
- Filter combinations return only matching, visible items.
- A query-count guard: list and detail requests each run a bounded number of SQL statements (no N+1).

**Done when**
- [ ] All tests above pass
- [ ] **HTTP-level confidentiality:** `GET /items/{key}` returns 404 (not 403) to a non-lead member of a Compliance team for a confidential item, and the item is absent from `GET /items`, facets and search (phase 2 could only prove this at policy and SQL level; full-text search arrives in phase 5 and joins the same test then)
- [ ] Membership and login commands run on `command_tx` (lock and statement timeouts, deadlock retry), as CLAUDE.md I5 requires
- [ ] `/api/docs` shows every endpoint with typed request and response models
- [ ] No `OFFSET` anywhere (`grep -rn "offset(" api/app` finds nothing in request paths)

**Failure modes guarded**
- Idempotency as check-then-insert (SELECT, then INSERT), letting two concurrent creates through.
- Idempotency stored outside the business transaction, so the item and the key can disagree after a crash.
- OFFSET pagination, or a cursor without the id tiebreak, silently skipping rows.
- An edit path that forgets to write history.

---

## Phase 4 — Workflow, ownership, approvals

**Goal:** the heart of the brief. Every critical behaviour except async lives here.

```text
Implement SPEC §4 and §6.1.

Workflow (app/domain/workflow.py — pure, no I/O)
- The transition table from SPEC §4.1 as data (one row per action/from-state), not as scattered if-statements.
- `evaluate(action, item_facts, relationship, payload) -> Allowed | Violation(code, message)` covering required reasons, resolutions, duplicate_of, and the approval requirement for resolve.
- `allowed_actions(item_facts, ctx)` = policy ∩ workflow; used by GET /items/{key} so the UI never re-implements rules.
- `next_step(item_facts, ctx)` per SPEC §4.5.

Commands (app/services/commands.py), each one command_tx with the SPEC §6.4 shape, row locked first:
- claim: the conditional UPDATE in SPEC §6.1. Zero rows → already mine 200 / someone else's 409 ALREADY_CLAIMED with current owner and claimed_at.
- release, assign, unassign (with team-membership check on the target user).
- transition: block, unblock, resolve, reopen, close, withdraw — reasons and resolutions per the table; reopen target rule.
- transfer per SPEC §4.3 (assignee kept only if a member/lead of the target; pending approval cancelled; approved invalidated; key unchanged; prev_team_id in NOTIFY payload).
- approvals: request (stores subject_hash = sha256 of team_id, type, title, description), decision (If-Match REQUIRED; four-eyes; lead of the item's team), cancel.
- In PATCH (from phase 3): when a material field changes, invalidate per SPEC §4.4 in the same transaction. This replaces the temporary 409 of phase 3 (ENGINEERING_DECISIONS 27) and re-adds `edit_text`/`edit_type` to `allowed_actions` for items with an approval.
- Already built in phase 3 (do not redo): `type_defaults`, `plan_edit`, `required_actions` and `next_step` in `workflow.py`, and `allowed_actions` for the field-edit actions (`services/items.py`). Phase 4 adds the transition table and merges the workflow actions into the same list.
- Map the DB four-eyes and one-pending constraints to 403/409 in case policy is ever bypassed.

Also: a test helper that replays seeded histories through workflow.evaluate.
```

**Tests**
- **T-FLOW:** every action × every status × every relationship (requester, assignee, member, lead, viewer, admin, outsider), against an expected table written by hand in the test from SPEC §4.1/§5.2.
- **T-CLAIM:** 20 claims at once, each on its own connection, released together by an `asyncio.Barrier` → exactly 1 × 200, 19 × 409, exactly one `assigned` event, version + 1.
- **T-APPROVE-RACE:** 50 rounds of "lead approves" racing "member edits description" → after each round, if an approval is `approved`, its subject_hash equals the hash of the current content. Never a stale approval.
- Resolve a `requires_approval` item without an approval → 422 `APPROVAL_REQUIRED`; with an invalidated approval → 422.
- The person who requested approval can't approve it (policy 403; and the DB check rejects it if the policy is bypassed in the test).
- Two concurrent approval requests → one 201, one 409 `APPROVAL_ALREADY_PENDING`.
- Transfer: assignee not in target team → unassigned and `new`; a previous-team member now gets 404; the requester still sees it.
- Release while awaiting approval → 409 `WORKFLOW_VIOLATION`.
- All seeded histories replay through `workflow.evaluate` without a violation.

**Sabotage check** (on a scratch branch, never committed; record results in `docs/TESTING.md`):

| Remove this guard | This test must go red |
|---|---|
| `assignee_id IS NULL` in the claim UPDATE | T-CLAIM |
| `version = :expected` in PATCH | T-STALE |
| approval invalidation on material edit | T-APPROVE-RACE |
| `If-Match` check in approve | T-APPROVE-RACE |
| approval check in resolve | T-FLOW (resolve rows) |

**Done when**
- [ ] Removing or demoting a team member unassigns their open items in the same transaction (SPEC 4.3b), replacing the phase 2 refusal in `app/services/teams.py` (ENGINEERING_DECISIONS 14)
- [ ] A PATCH of a material field invalidates a pending or approved approval in the same transaction (replaces the temporary 409 from decision 27)
- [ ] All tests pass, and every sabotage row was observed red
- [ ] `GET /items/{key}` returns correct `allowed_actions` and `next_step` for each demo persona on a sample of items
- [ ] Independent review (see top of file) run and confirmed findings fixed

**Failure modes guarded**
- Check-then-act claiming (SELECT, then UPDATE) → two owners.
- Approving content that changed between opening and clicking Approve.
- Workflow rules scattered across endpoints, so one path forgets a rule.

---

## Phase 5 — Collaboration, attention, search, dashboard API

**Goal:** everything a user needs to understand an item and find work, all through the visibility rule.

```text
Implement SPEC §7 (attention only), the search part of §8, and these endpoints from §11: comments, watch, read, me/attention, me/notifications (+ read), items/similar.

Scope change: GET /stats/teams and GET /decisions are NOT built here. They are deferred to phase 11 together with the dashboard and decisions screens.

- Comments: idempotency key required; append-only; writes a `commented` event (data: comment_id) via record_event; the author auto-watches.
- item_reads: POST /items/{key}/read upserts last_read_event_id (the item's current last_event_id); item DTO includes unread_since_event_id (the caller's last_read_event_id, null if never opened).
- Attention: one indexed query per section with LIMIT, counts capped at 100.
- Search: key-shaped q → exact key; else websearch_to_tsquery + ts_rank, plus trigram similarity on title; top 50; visibility applied INSIDE the query.
- Similar: trigram similarity > 0.35, open items in the same team, top 5.
- (Stats and decisions moved to phase 11; when built they use the visibility clause, so a member and a lead see different numbers.)
- Timeline endpoint returns events with the comment body joined for `commented` events.
```

**Tests**
- **T-VIS extended:** search, facets, attention, similar and notifications never return or count an item the caller can't view (property test over all demo users). Stats and decisions join it in phase 11.
- Attention: each section's rule, using fixtures built for it (an item that should and one that shouldn't appear).
- Search: key jump; a title match ranks above a description-only match; a typo ("refnd") still finds "refund" via trigram.
- Comments can't be updated or deleted (no route; DB-level check on the events side already exists).
- Query-count guard on attention and list.

**Done when**
- [ ] The search endpoint (`q` on `GET /items`) joins the phase 3 HTTP confidentiality test (`tests/integration/test_items_create_read.py`), together with attention and notifications: a non-lead Compliance member never sees the confidential item there. Remove `q` from the "unknown parameter" refusal (ENGINEERING_DECISIONS 29).
- [ ] All tests pass on the demo seed
- [ ] (Moved to phase 11) A lead and a member of Payments get different `stats/teams` numbers when confidential items exist, and both match what they can list

**Failure modes guarded**
- One query that skips the visibility clause (search, counts and dashboards are where this usually leaks).
- Attention queries that scan the entire history and slow down as data grows.

---

## Phase 6 — Async worker

**Goal:** secondary work that happens after the user's action, is never lost, survives failures, and is safe to run twice.

```text
Implement SPEC §9.

- app/worker/runner.py: a loop that claims a batch with the SPEC §9 query (FOR UPDATE SKIP LOCKED, 60 s lease), dispatches to handlers by topic, marks done, or schedules a retry with exponential backoff + jitter, and marks dead after 8 attempts. Graceful shutdown on SIGTERM: finish the current job, stop claiming.
- Handlers: notify (fan-out with ON CONFLICT DO NOTHING on (user_id, event_id); skip the actor; NOTIFY notifications {user_id}), duplicates (item.created → item_similar upsert + one duplicate_suggested event, dedupe key), SLA sweep (every 60 s, SPEC §9 statement, inside pg_try_advisory_xact_lock), cleanup (hourly).
- The worker logs carry the request_id from the outbox payload.
- FAULT_NOTIFY_FAIL_RATE in dev only.
- Admin: GET /admin/jobs?status=, POST /admin/jobs/{id}/retry (sets pending, attempts 0, available_at now).
- Compose: the worker service runs the real runner; it can be scaled (`docker compose up --scale worker=2`).
```

**Tests**
- **T-OUTBOX concurrency:** two runners against 500 jobs → each job's side effect happens exactly once.
- **Crash:** a handler that hangs past the lease → another runner picks the job up after expiry; the side effect still happens once (idempotent handler).
- **Failure path:** a handler that always raises → attempts increase with backoff (use time-machine), dead after 8, visible via `/admin/jobs?status=dead`; retry endpoint requeues it.
- **Same job delivered twice** → one notification per user.
- **SLA sweep run twice at once** → exactly one `sla_breached` event per overdue item.
- **Rollback:** a command that fails after inserting into outbox leaves no outbox row.

**Done when**
- [ ] All tests pass; `docker compose up --scale worker=2` processes the demo backlog with no duplicate notifications
- [ ] Demo check: stop the worker, make changes in the API, start it again → notifications catch up
- [ ] Independent review run and confirmed findings fixed

**Failure modes guarded**
- Dual writes (commit, then enqueue elsewhere) losing work when the process dies in between.
- Duplicate notifications from at-least-once delivery without idempotent handlers.
- Scheduled jobs running once per process when more than one process runs.
- A poison job blocking everything behind it.

---

## Phase 7 — Live updates (SSE)

**Goal:** people looking at an item or a list see other people's changes within a second or two, without leaking anything they can't see.

```text
Implement SPEC §10.

- app/realtime/listener.py: one dedicated asyncpg connection per API process (not from the SQLAlchemy pool) running LISTEN item_changes and LISTEN notifications; reconnect with backoff; on reconnect, broadcast `resync`.
- app/realtime/hub.py: per-client bounded asyncio.Queue (size 1,000); filter each event with policy.can_view on the payload facts and the client's actor context (refreshed every 60 s); full queue → drop backlog, send resync.
- GET /api/v1/stream: requires session; sets Cache-Control: no-cache and X-Accel-Buffering: no; `id:` = event_id; 15 s heartbeat comments; on Last-Event-ID, replay visible events since that id from item_events (max 500), else send resync.
- Confirm the nginx location for /api/v1/stream has buffering off.
```

**Tests**
- User A changes an item → user B (same team) receives `item.changed` within 1 s, **through nginx** in the compose stack.
- A confidential item's change is never delivered to a non-lead member; an item from another team is never delivered.
- Reconnect with `Last-Event-ID` replays the missed events; a gap of more than 500 → `resync`.
- A client that doesn't read → `resync` after the queue fills, and server memory stays flat.
- Transfer → the previous team's member receives the event and their next GET is 404.

**Done when**
- [ ] All tests pass through nginx, not only against uvicorn directly
- [ ] Two browser tabs (curl is fine) show live events in real time

**Failure modes guarded**
- Proxy buffering: everything works on the API port and silently doesn't through nginx.
- LISTEN on a pooled or transaction-mode connection, so events stop arriving.
- Content leaking through the event stream instead of going through authorized fetches.
- Unbounded per-client buffers.

---

## Phase 8 — Frontend foundation and design system

**Goal:** the look, the shell, and a typed, safe connection to the API, before building any screen.

```text
Read docs/DESIGN.md fully before starting.

- web/src/styles/tokens.css with every token in DESIGN §2 for both themes; Tailwind v4 @theme mapping; no raw hex anywhere else (add an ESLint rule or a grep check in CI).
- Self-host Atkinson Hyperlegible Next (DESIGN §2.3). Verify tabular figures; if unsupported, keep the face anyway.
- API client: openapi-typescript + openapi-fetch generated from the API's OpenAPI (`npm run gen:api`); CI regenerates and fails on a diff.
- lib/api.ts wrapper: credentials: 'include', X-CSRF-Token on unsafe methods, parse problem+json into a typed ApiError {status, code, detail, current?, changes_since?, request_id}.
- QueryClient defaults: queries retry network errors and 5xx twice with backoff, never 4xx; mutations don't retry by default (phase 9 adds the idempotent retry).
- Routing (React Router): /login, /inbox (default), /items, /items/:key, /dashboard, /decisions, /teams/:key/settings, /admin/jobs, /dev/ui (dev only).
- Auth: bootstrap from GET /me; 401 → /login?next=…; sign out.
- App shell per DESIGN §3 with breakpoints; error boundary per route; Sonner toasts themed; aria-live region; keyboard shortcut manager (ignores typing in fields); command palette shell (cmdk).
- Every component in DESIGN §8 with all its states, shown on /dev/ui in both themes.
```

**Tests**
- Vitest: ApiError parsing for each SPEC §12 shape; CSRF header added only to unsafe methods; 401 redirect keeps the return path.
- Playwright: screenshots of `/dev/ui` in light and dark at 1280 px; axe on `/dev/ui` and `/login` with no serious violations.

**Done when**
- [ ] Claude Code took the `/dev/ui` screenshots **and looked at them**, compared them with DESIGN.md, and fixed what didn't match (list the fixes)
- [ ] No raw hex outside `tokens.css`; the CI generated-types check passes
- [ ] Login works against the real API in the compose stack

**Failure modes guarded**
- A generic look (default Tailwind palette, Inter, cards everywhere) instead of DESIGN.md.
- Hand-written API types drifting from the server.
- Design tokens bypassed with one-off colours.

---

## Phase 9 — Inbox, queue, search, new request

**Goal:** finding work and raising requests, with server state handled correctly.

```text
Implement DESIGN §4.1–4.3, §4.5, §4.12 and the client rules in SPEC §6.5–6.6.

Core client logic (write and unit-test these first)
- lib/itemCache.ts: mergeItem(cached, incoming) → keep the higher (version, last_event_id) (never roll back); upsert into the detail cache and every list cache containing the item; remove from lists on 404.
- lib/useCommand.ts: a mutation wrapper that
  - creates the Idempotency-Key when the user acts and reuses it for every retry of that action;
  - sends If-Match from the cached version for commands that need it (SPEC §6.2 table);
  - runs with TanStack Query `scope: { id: 'item:' + id }` so actions on one item run one after another;
  - retries only on network errors and 503 BUSY (max 3, backoff), with the same key;
  - on success: mergeItem(server response) and invalidate lists, facets and attention;
  - on error: maps codes to the behaviour in SPEC §12 and copy in DESIGN §6.2.

Screens
- Strip component (DESIGN §4.1), rail with counts, Inbox (§4.2), Queue (§4.3) with filters in the URL (parsed and validated with zod), facets in menus, sort, infinite virtualized list (TanStack Virtual), j/k navigation with prefetch, search as you type, "N items changed — Refresh order" bar.
- New request dialog (§4.5) with similar-request suggestions while typing, type defaults, due preview, pending state.
- Every state from DESIGN: loading, empty, error.
```

**Tests**
- Vitest: `mergeItem` ignores older versions; `useCommand` reuses one key across retries and sends `If-Match`; two queued commands on one item run in order with the updated version; URL ↔ filter round trip.
- Playwright: create a request → it appears in the queue; **double-click Create with the first response dropped** (route interception) → exactly one item exists.

**Done when**
- [ ] All tests pass; queue scrolls smoothly through 1,000+ items on the large seed
- [ ] Screenshots of Inbox, Queue and New request reviewed against DESIGN.md (light and dark, 1280 and 375 px)

**Failure modes guarded**
- A new idempotency key per retry, which turns a retry into a duplicate.
- The list reordering under the user's cursor when live data arrives.
- Filters in component state, lost on refresh and unshareable.
- Loading everything into the browser and filtering client-side.

---

## Phase 10 — Item detail, actions, approvals, conflicts, live

**Goal:** the screen where the critical behaviours become visible to a user.

```text
Implement DESIGN §4.4, §5, §6 and SPEC §6.6.

- Detail pane/page with header, HandoffTrack (built from assigned/unassigned/transferred events, DESIGN §5, with its accessible list), action bar rendered ONLY from allowed_actions with the primary action from next_step, reason popovers for actions that need one.
- Properties with inline editing: optimistic for priority and watch (roll back on error); confirmed (spinner) for claim, assign, transitions, approvals.
- Description editor with a local draft; when a live update changes the item while a draft exists, show the banner (DESIGN §6.2) and keep the draft.
- 412 handling: lib/rebase.ts — if my changed fields and their changed fields (from changes_since) don't overlap, resend on the new version and toast; otherwise open ConflictDialog with a word-level DiffView (jsdiff).
- Approval panel, including the stale-approve flow: 412 on approve → show changes → "Approve this version".
- Timeline with All/Comments/Decisions filter, "New since you last looked" divider, composer with optimistic comments ("Sending…"), POST /read when opened.
- lib/useLiveUpdates.ts: EventSource with reconnect; coalesce item.changed per item over 250 ms; refetch + mergeItem; on resync invalidate everything on screen; on 404 after an event, show "moved / no longer have access" and close.
- aria-live announcements for live changes; assertive for conflicts.
```

**Tests**
- **T-RECONCILE** (Vitest, together with the phase 9 `mergeItem` and scope tests): rebase with disjoint vs overlapping fields; ConflictDialog choices produce the right request; coalescing turns 20 events in 100 ms into one refetch; `allowed_actions` drives which buttons render.
- Playwright, two browser contexts (two users):
  1. **Claim race:** both click "Assign to me" → one sees "Assigned to you", the other "… took PAY-xxx …", and both screens end showing the same owner.
  2. **Stale edit:** A edits the description while B saves a new description → A gets the conflict dialog with a diff.
  3. **Disjoint edit:** A changes priority while B changes the title → both saved, no dialog.
  4. **Approve after edit:** the lead opens the approval, the member edits the description, the lead clicks Approve → "changed since you opened it" → review → approve.
  5. **Live:** B changes priority → A's open detail and list update within 2 s without reload.

**Done when**
- [ ] All tests pass
- [ ] Screenshots of the detail view in each status, both themes, reviewed against DESIGN.md
- [ ] Independent review run and confirmed findings fixed

**Failure modes guarded**
- Self-conflict: one user's two quick actions racing each other.
- A slow, older response overwriting a newer screen.
- Losing the user's draft when a live update arrives.
- Workflow rules re-implemented in the UI instead of read from `allowed_actions`.

---

## Phase 11 — Dashboard, decisions, notifications, team admin, jobs

**Goal:** the management view and the admin tools, with authorization visible in the UI and enforced by the API.

```text
Implement DESIGN §4.6–4.11.

Backend first (moved here from phase 5): GET /stats/teams (SPEC §7 dashboard) and GET /decisions (decision log), both through visibility_clause(), with a query-count guard and the T-VIS property test extended to them.

- Dashboard: org figures, team table with open-by-priority bars, unowned, overdue, awaiting approval, median age, 14-day created/resolved sparkline; every number links to the filtered queue. Follow the dataviz rules (one hue per series, direct labels).
- Decision log with team and kind filters.
- Notifications popover with unread count updated by the stream; mark read.
- Team settings: membership table and role changes per SPEC §5.2; warn when removing someone who owns open items.
- Jobs page (admin): tabs, error details, Retry.
- User menu: theme (system/light/dark), density, sign out. Demo persona list on the login page when DEMO_MODE.
```

**Tests**
- Playwright: a viewer sees no edit controls on Team settings, **and** the same request sent directly with `page.request` returns 403.
- Dashboard numbers for a lead vs a member differ exactly by the confidential items, and each matches what that user can list.
- Retrying a dead job from the Jobs page processes it.

**Done when**
- [ ] All tests pass; screenshots reviewed against DESIGN.md

**Failure modes guarded**
- Dashboard counts that include items the viewer can't open.
- Admin actions protected only in the UI.

---

## Phase 12 — Scale and performance proof

**Goal:** evidence, not claims, that Baton holds up at the brief's scale.

```text
Load the large seed (SPEC §13.1) and measure.

- api/scripts/bench.py: httpx async load generator, 20 concurrent virtual users for 60 s, a realistic mix (list with filters, item detail, search, attention, PATCH, claim, comment), reporting p50/p95/p99 and error rate per endpoint, cold and warm.
- EXPLAIN (ANALYZE, BUFFERS) for every request-path query; save the plans in docs/perf/.
- tests/perf/test_query_plans.py (marker `perf`, run against the large seed): no Seq Scan on work_items or item_events in request-path queries.
- Fix what's slow (index column order, partial indexes, query shape) and re-measure.
- Frontend: route-level code splitting; report the initial JS size; check scrolling 1,000 rows; simulate a burst of 200 live events/s and confirm no re-render storm (React Profiler).
- Write docs/PERFORMANCE.md: machine spec, dataset sizes, results table vs SPEC §13 targets, what was changed to get there, and what would break first at 10× scale.
```

**Done when**
- [ ] Every SPEC §13 target is met, or the miss is explained in PERFORMANCE.md with the plan to fix it
- [ ] The query-plan test passes on the large seed
- [ ] PERFORMANCE.md has real numbers from this machine

**Failure modes guarded**
- Indexes that look right but the planner doesn't use (wrong column order for the sort).
- Numbers measured only on tiny data, or only warm.

---

## Phase 13 — End-to-end tests, sabotage checks, hardening

**Goal:** prove the dangerous behaviours end to end, prove the tests aren't vacuous, and close the gaps in errors, security and accessibility.

```text
- Playwright suite running against the compose stack (profile e2e, Playwright image) in CI: the five two-user scenarios from phase 10, double-submit create, viewer API 403, live update, offline banner, and a keyboard-only run of create → claim → resolve.
- Complete docs/TESTING.md: risk → test map for CB1–CB8 (SPEC §14) and the sabotage table (guard → test → observed red, with the date), extended to the frontend guards (version guard in mergeItem, mutation scope, same-key retries).
- Failure drills, each written up in TESTING.md: stop Postgres mid-request (503 then clean recovery, no half-writes); stop the worker (app keeps working; notifications catch up); kill the API during a command (the retry with the same key replays or completes, never duplicates); browser offline.
- Security pass: CSRF on all unsafe routes (already automated); cookie flags; CSP and security headers from nginx; Markdown XSS test (`<img src=x onerror=alert(1)>` and `javascript:` links render inert); login throttling; no stack traces in responses.
- Accessibility: axe on Inbox, Queue, Detail, New request, Dashboard (no serious/critical violations); focus order; screenshots at 375, 768 and 1280 px.
- Remove fixed sleeps from tests; use polling assertions.
```

**Done when**
- [ ] The full E2E suite is green in CI three runs in a row (no flakes)
- [ ] Every sabotage row in TESTING.md was observed red
- [ ] Every failure drill is written up with what happened
- [ ] Independent review run and confirmed findings fixed

**Failure modes guarded**
- Vacuous tests that pass even with the guard removed.
- Flaky E2E tests hiding real failures.
- XSS through item descriptions or comments.

---

## Phase 14 — Documentation, demo script, release

**Goal:** everything the brief's Submission and Final Discussion sections ask for, verified from a clean clone.

```text
Write the documents below. Every claim must point to code or a test; no claim without evidence.

- README.md: one-line pitch; screenshot and a 30-second GIF; Quick start (`docker compose up --build`, open http://localhost:8080); demo accounts table; "A five-minute tour" that walks through each critical behaviour; how to run tests; project layout; Assumptions (SPEC §1); links to the other docs.
- docs/ENGINEERING_DECISIONS.md — six decisions in ADR form (Context, Decision, Alternatives considered, Trade-offs, When I'd revisit it):
  1. PostgreSQL for state, queue, search and pub/sub — no Redis, Kafka or Elasticsearch.
  2. Concurrency: optimistic versions for edits, atomic conditional updates for claims, no locks held across user think-time.
  3. Commands for state changes, PATCH for fields; workflow and permissions as pure modules shared by the API, list queries and the UI (allowed_actions).
  4. Idempotency keys stored in the same transaction as the change.
  5. One event log as audit trail, activity feed and outbox source; not full event sourcing.
  6. What I chose not to build, and why.
- docs/ARCHITECTURE.md: Mermaid component diagram; sequence diagram of one command (lock → policy → workflow → change → event → outbox → NOTIFY → commit); async flow; live-update flow; scaling path at 10× and 100×.
- docs/TESTING.md (from phase 13), docs/PERFORMANCE.md (from phase 12).
- docs/KNOWN_LIMITATIONS.md: SPEC §15 plus anything found during the build, each with what it would take.
- docs/DEMO_SCRIPT.md:
  - a 10-minute demo flow (login as a lead → inbox → claim race in two windows → stale edit → approval invalidation → stop the worker and restart it → viewer tries the API directly → search on the large seed → run the tests and show one sabotage);
  - prepared answers to the six Final Discussion questions in the brief;
  - "likely change requests" with where each change goes and which tests protect it: add a status, add a role, per-team SLA, @mentions, bulk reassign, email notifications.
- Clean-clone check: clone into a new folder, follow README literally, run every documented command; fix anything that only worked on this machine.
- Final checks: docs/REQUIREMENTS_TRACE.md fully ticked with proof links; gitleaks clean on full history; the brief PDF is not in the repo; the repo is public (or reviewers have access).
```

**Done when**
- [ ] A clean clone runs from the README alone, and all tests pass from it
- [ ] Every submission item in the brief is present: working code, run instructions, setup, decisions document, tests, known limitations (+ diagrams)
- [ ] REQUIREMENTS_TRACE.md has no unticked row
- [ ] You have rehearsed the demo once end to end and practised two change requests from DEMO_SCRIPT.md live

**Failure modes guarded**
- README steps that only work on your machine.
- A decisions document that lists features instead of trade-offs.
- Being unable to explain or change code you submitted.
