# Notes for the interview

Written for Kumar, to explain any part of Baton live.

## Problems hit

Each entry: **problem → root cause → fix → lesson**.

### Phase 0

**1. Tests ran old code and still looked fine**
- Problem: after fixing a bug, `api-test` failed with the same assertion as before.
- Root cause: `docker compose run` reuses an existing image; it never rebuilds. The container had the old source baked in.
- Fix: `api-test` mounts `./api`, `web-test` mounts `./web/src`. Dependencies live in `/opt/venv` and `node_modules`, outside the mount, so the mount can't hide them. Dependency or Dockerfile changes still need `--build` (recorded in CLAUDE.md).
- Lesson: the failure mode here is the same family as skipped tests: a green or red result that doesn't describe the current code. Make "tests ran current code" structural, not a habit.

**2. The access log lost `/api/v1` from the route**
- Problem: log line said `route: "/healthz"`, test expected `/api/v1/healthz`.
- Root cause: FastAPI 0.142 resolves `include_router(prefix=...)` lazily; `scope["route"].path` is the inner route without the prefix. Only a prefix declared on the `APIRouter` itself survives (checked three wirings).
- Fix: routers declare `APIRouter(prefix=API_PREFIX...)`; shared constant in `app/api/constants.py`.
- Lesson: when a framework upgrade changes internals, test the observable output (the log line), not the wiring.

**3. `npm install` failed with ERESOLVE**
- Problem: ESLint 10 and `eslint-plugin-jsx-a11y` conflict.
- Root cause: jsx-a11y's peer range stops at ESLint 9.
- Fix: pin `eslint@9` and `@eslint/js@9` instead of `--force` or `--legacy-peer-deps`. Recorded as Decision 4.
- Lesson: resolve a peer conflict by choosing a version, not by silencing the resolver.

**4. Port 8080 was already taken**
- Problem: `web` failed to bind 8080.
- Root cause: an unrelated project's container (`infra-latex-1`) publishes it.
- Fix: host port is `${WEB_PORT:-8080}`; default unchanged; verified on 8081 and 8082. Not stopped, since it isn't ours. Recorded as Decision 3; the 8080 check is deferred to phase 14.
- Lesson: say "unverified on 8080" rather than quietly changing the target.

**5. CI "gitleaks" step failed, while reporting "no leaks"**
- Problem: `hygiene` job red; the log said "no leaks found in partial scan".
- Root cause: the gitleaks action scans `<first-commit>^..HEAD`; a root commit has no parent, so git errored and 0 bytes were scanned. A green result there would have meant nothing.
- Fix: run the pinned gitleaks CLI over the full history (same version as pre-commit). 12 commits scanned.
- Lesson: "no findings" is only evidence if you know how much was scanned. Check the byte or commit count.

**6. Two Git Bash traps**
- A large heredoc with mixed quotes failed to parse (nothing ran, so nothing was half-written). Use the file-writing tool for source files.
- `docker run -v "$(pwd -W):/repo"` was rewritten to `C:/Program Files/Git/repo`. Fix: `MSYS_NO_PATHCONV=1`.

### Phase 1

**1. Pytest failed on an Alembic warning**
- Problem: the first database test run errored at setup with a `DeprecationWarning` from Alembic.
- Root cause: `filterwarnings = ["error"]` turns warnings into failures, and Alembic wants `path_separator` in `alembic.ini`.
- Fix: `path_separator = os`. Kept the strict setting.
- Lesson: error-on-warning is a cheap tripwire for config drift; fix the cause, never loosen the filter.

**2. `str.format()` on SQL**
- Problem: building the event-kind CHECK with `.format()` would have crashed on the `'{}'::jsonb` default in the same string.
- Root cause: braces mean two things in one string.
- Fix: a placeholder token and `.replace()`. Caught on review before the first run.
- Lesson: do not template SQL with `format`; keep constants and use tokens.

**3. COPY cannot write `citext`**
- Problem: `asyncpg.InternalClientError: no binary format encoder for type citext`.
- Root cause: asyncpg's binary COPY has no codec for extension types.
- Fix: users (at most 2,000 rows) go in with `INSERT ... $2::text::citext`; everything else uses COPY.
- Lesson: bulk-load fast where the driver can, and use the boring path for the one odd type.

**4. The verifier "found" a bug in valid data**
- Problem: it reported "status in_progress has no owner" after an `unassigned` event.
- Root cause: a command writes several events at one version (`unassigned` then `status_changed`); the state between them is never observable. The verifier checked after every event.
- Fix: database rules are checked at command boundaries; field continuity and status rules still run per event.
- Lesson: a validator must check what the system can actually observe, or it rejects correct data.

**5. The simulator tried an invalid move**
- Problem: seeding stopped with `InvalidTransition: requires_approval is not allowed from awaiting_approval`.
- Root cause: my random "noise" ignored the transition table.
- Fix: noise now asks the table before acting.
- Lesson: because every command goes through one guarded table, a generator bug surfaces as an exception, not as bad data in the database.

**6. A test assumed a snapshot, not the rule**
- Problem: "all Compliance items are confidential" failed.
- Root cause: leads may legitimately switch it off later (SPEC 4.2); the rule is "confidential at creation".
- Fix: the test checks the created event, and that most are still confidential.
- Lesson: assert the rule, not what one generated dataset happens to look like.

**7. The documented migration command could not work**
- Problem: `docker compose run --rm migrate alembic revision` failed with `PermissionError`.
- Root cause: the image runs as a non-root user and nothing was mounted, so even success would have been lost with the container.
- Fix: mount `api/alembic` and run `migrate` as root (it only talks to the database).
- Lesson: run each documented command once before documenting it.

**8. My own patch scripts silently did nothing**
- Problem: `--dry-run` was "unrecognized" right after I added it.
- Root cause: a `str.replace` whose pattern had been reformatted by ruff did not match, and I did not check.
- Fix: every scripted edit now asserts the pattern matched.
- Lesson: a tool that can no-op silently needs an assertion around it.

**9. Comments bumping the version would have caused a 412 storm**
- Problem (found in review, not in code): with "every write bumps `version`", one comment makes everyone else's `If-Match` stale.
- Decision (Kumar): the version counts decision-relevant state only (title, description, type, priority, status, assignee, team, due_at, confidential, requires_approval, approval state, resolution). Comments and system events keep the current version (decision 8).
- Knock-on: unread moved from versions to event ids (migration 0002, with a data-conversion test); `changes_since` lists versioned events only; the timeline cursor is `after_event_id`; the client guard compares `(version, last_event_id)`.
- Lesson: decide what a concurrency token protects before wiring it everywhere.

**10. An invariant was only in prose (found at close-out)**
- Problem: SPEC 3.2 says `teams.key` is immutable and `work_items.key` never changes, but nothing in the database enforced it (I10).
- Fix: migration 0003, `BEFORE UPDATE` triggers; four new registry entries.
- Lesson: go through the invariants against the diff, not from memory.

**11. First large seed did not match the SPEC sizes**
- Problem: 496k events and 172k comments against SPEC 13.1's 600k and 120k.
- Fix: a `--dry-run` flag (generate and count, no database, 20 s) made tuning cheap; now 587k and 128k, 18,107 open.

**12. ruff sorted imports wrongly**
- Root cause: with `src = ["."]` it treated the local `alembic/` folder as first-party.
- Fix: `known-third-party = ["alembic"]`.

## Explain this phase

### Phase 0: foundation

1. **What was built.** An empty but real stack: Postgres, a placeholder `migrate`, a FastAPI API, a heartbeat worker, and a React page behind nginx. Tests run in containers against a real tmpfs Postgres. CI runs lint, types, tests, build, and repository hygiene (LF, no PDF/`.env`, gitleaks).
2. **Key decision: tests fail, never skip, when there is no database.** `pytest_sessionstart` in `api/tests/conftest.py` calls `pytest.exit(..., returncode=2)` if `TEST_DATABASE_URL` is missing.
3. **Alternative.** `pytest.mark.skipif` or a fixture that skips. That is how a suite reads "130 passed, 16 skipped" and still looks green.
4. **What breaks if the guard is removed.** Database tests silently disappear on a machine without the variable, and CI would stay green without proving anything. `test_pytest_fails_instead_of_skipping_when_test_database_url_is_unset` runs pytest in a subprocess without the variable and goes red.
5. **Where it lives.** `api/tests/conftest.py:22`.
6. **Second guard: prod refuses demo and fault injection.** `refuse_demo_and_faults_in_prod` in `api/app/config.py:26`. Without it, `ENV=prod` with `DEMO_MODE=true` would expose the account switcher. "Set" is read as enabled (Decision 2); tests in `tests/unit/test_config.py`.
7. **Third guard: request ids.** `resolve_request_id` in `api/app/api/request_context.py:22` only accepts `^[A-Za-z0-9._-]{1,128}$` (line 17), because the id goes into logs and headers. Remove it and a client could inject arbitrary text into a header or log line. Crashes also return problem+json with the id (`:77`).
8. **Fourth guard: LF endings.** `.gitattributes:1` (`* text=auto eol=lf`) and the CI `hygiene` job. Without them a CRLF entrypoint fails in Linux with `/bin/sh^M: not found`.
9. **nginx.** `/api/v1/stream` has `proxy_buffering off` and a 1 h read timeout, so SSE (phase 7) isn't buffered or cut off. `X-Request-ID` is forwarded, or generated by nginx if the client sent none.
10. **Not done on purpose.** No schema, auth, CSRF or CSP yet; each belongs to a later phase.


### Phase 1: schema, migrations, seed data

1. **What was built.** All of SPEC section 3 in Postgres as three hand-written migrations (tables, enums, indexes, constraints, triggers), a SQLAlchemy Core mirror, and a deterministic seed (`demo`: 600 items; `large`: 50,000 items, 586,983 events) with an independent verifier that replays every history.
2. **Key decision: the database itself rejects impossible states.** Named CHECKs, a partial unique index and triggers, so a buggy code path, a script or someone's SQL cannot write an in-progress item with no owner, two pending approvals, an approver who is the requester, an edited event, or a renamed key.
3. **Alternative.** Check these only in Python. Friendlier errors, but nothing stops the first path that forgets a check.
4. **How it is proved.** `tests/db/violations.py` lists 28 violations. Each is tested twice: rejected under that guard's own name, and, with the guard dropped inside the test transaction, accepted. So no test passes because something else happened to fail.
5. **What breaks if the main guard is removed.** Delete `owner_when_active` and 5 tests go red (done by hand, see TESTING.md); delete the trigger and events can be rewritten.
6. **Where the guards live.** `api/alembic/versions/0001_initial_schema.py:157` (`owner_when_active`), `:209` (four-eyes), `:321` (one pending approval), `:339` (append-only trigger); `0003_identity_immutable.py:44` (identity triggers).
7. **The seed cannot invent impossible histories.** `ItemSim` only takes transitions in `api/scripts/seedlib/sim.py:29` (`TRANSITIONS`), and `scripts/seedlib/verify.py:101` (`check_item`) replays the stored events independently and compares with the stored row. All 50,000 large histories pass in 17 s.
8. **Version semantics (decision 8).** `version` counts decision-relevant state only; comments and system events keep the current one, so a comment never makes another editor's `If-Match` stale. "Unread" is therefore an event id (`item_reads.last_read_event_id`).
9. **Honest gaps.** TRUNCATE is not covered by the append-only trigger (KNOWN_LIMITATIONS); the seed writes `work_items` directly (decision 7); the `COALESCE(due_at)` index question waits for `EXPLAIN` (decision 10).
10. **Numbers.** 249 API + 4 web tests, none skipped. Demo seed 1.4 s; large seed 69 s wall time.
