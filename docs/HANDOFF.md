# Handoff

Where the project stands, how to run it, and what to know before the next session.

## Current state

**Phase 3 (work items core) is done. Phase 4 has not started.** Tags: `phase-3-start`, `phase-3-done`.

Repo: https://github.com/KumarPriyam123/baton (private). CI is green on `main` (api, web, hygiene): https://github.com/KumarPriyam123/baton/actions

What exists, on top of phases 0-2 (stack, schema, seed, identity, authorization):

- **Command transaction** (`app/db/tx.py`): `command_tx` (one attempt: `SET LOCAL lock_timeout 2s / statement_timeout 5s`, database errors become domain errors) and `run_command` (restarts the whole command up to 3 times on 40001/40P01, then 503 BUSY). `record_event()` writes event + `last_event_id` + outbox row + NOTIFY, and refuses an item whose version differs from the row. `CommandTx.restamp()` re-reads the clock after a lock wait.
- **Constraint mapping** (`app/db/errors.py`): named CHECKs and the one-pending index become SPEC 12 errors; a test fails if a new constraint is neither mapped nor declared a bug.
- **Idempotency** (`app/api/idempotency.py`): SPEC 6.3 exactly (insert `ON CONFLICT DO NOTHING` in the command transaction, savepoint, store 2xx and domain 4xx, roll back on 5xx and BUSY). `POST /items` requires the key, `PATCH` takes it optionally.
- **Items** (`app/repo/items.py`, `app/services/items.py`, `app/api/routers/items.py`): `POST /items`, `GET /items/{key}` (ETag, `allowed_actions`, `next_step`), `PATCH /items/{key}` (If-Match, 412 with `current` and `changes_since`), `GET /items` (filters, 4 sorts, keyset cursor, limit 50/100), `GET /items/facets`, `GET /items/{key}/events`.
- **Workflow rules so far** (`app/domain/workflow.py`, pure): type defaults, due dates, `plan_edit` (what an edit changes, reasons, events), `required_actions`, `next_step` (full SPEC 4.5 table).
- **Login, logout and membership** run on `command_tx`.
- **Docs/API:** every operation id is the route function name; `/api/docs` is fully typed (`tests/unit/test_openapi.py`).
- **Tests:** 3,129 API (unit, db, integration, concurrency) and 4 web; all need real Postgres and none skip. About 165 s.

Verified in this phase: every Done-when item (see `docs/REQUIREMENTS_TRACE.md`), CI green, the real stack on port 8081 (migration 0004, create, replay, list, `/api/docs`), EXPLAIN on the large seed, an independent review (decision 34).
**Still deferred:** `docker compose up` on port 8080 itself (another local project holds it); re-check at the phase 14 clean-clone run.

Not built yet, by design: workflow commands (claim, assign, transitions, transfer, approvals), comments/watch/read, search, attention, notifications, the worker, SSE, the web UI, stats and decisions endpoints.

## Commands

| Task | Command |
|---|---|
| Start everything | `docker compose up --build` (http://localhost:8080; if taken, `WEB_PORT=8081` in `.env` or the shell) |
| API tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` (about 165 s) |
| One test file | `... run --rm api-test pytest tests/integration/test_items_patch.py -q` |
| Web unit tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test` |
| After dependency or Dockerfile changes | add `--build`: `run --rm --build api-test` |
| Re-seed demo from scratch | `docker compose run --rm seed python -m scripts.seed --size demo --reset` |
| Large seed (**own database**) | `docker compose exec db createdb -U baton baton_large` once; then with `L=postgresql+asyncpg://baton:baton@db:5432/baton_large`: `docker compose run --rm -e DATABASE_URL=$L migrate` and `docker compose run --rm -e DATABASE_URL=$L seed python -m scripts.seed --size large --reset` (about 65 s) |
| Replay every history | `docker compose run --rm seed python -m scripts.verify_seed` (add `-e DATABASE_URL=$L` for the large one) |
| New migration | `docker compose run --rm migrate alembic revision -m "<msg>"` |
| Tear down (also stops `db-test`) | `docker compose -f compose.yaml -f compose.test.yaml down --remove-orphans` |
| API checks on the host | `cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests scripts` |
| Secret scan | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" zricethezav/gitleaks:v8.30.1 detect --source /repo --no-banner` |

Demo accounts (all `@baton.test`, password `baton-demo`, from `GET /api/v1/demo/users`): `priya.lead` (Payments lead), `asha` and `rahul` (Payments members), `meera` (Support member, Payments viewer), `dev.viewer` (viewer on Payments and SRE), `farah` (Compliance member: cannot see others' confidential items), `ishaan.lead` (Compliance lead), `sunita.lead` (Support lead), `admin`. Two leads per team.

Trying the items API by hand (sign in with a cookie jar, send `baton_csrf` as `X-CSRF-Token`, a new `Idempotency-Key` per create):

```bash
curl -c jar -X POST localhost:8080/api/v1/auth/login -H 'content-type: application/json' \
     -d '{"email":"meera@baton.test","password":"baton-demo"}'
CSRF=$(grep baton_csrf jar | awk '{print $7}')
curl -b jar -X POST localhost:8080/api/v1/items -H "X-CSRF-Token: $CSRF" -H "Idempotency-Key: $(date +%s)" \
     -H 'content-type: application/json' -d '{"team_key":"PAY","type":"incident","title":"Refund stuck"}'
curl -b jar 'localhost:8080/api/v1/items?status=new&sort=updated&limit=5'
```

## Hazards

- **Port 8080** is held by `infra-latex-1` (an unrelated project). Do not stop it. Use `WEB_PORT`.
- **The large seed goes into `baton_large`, never `baton`** (CLAUDE.md). `seed --reset` TRUNCATEs everything; in phase 3 it overwrote the demo data once.
- **`compose run` never rebuilds.** `api-test` and `web-test` mount the source; dependency or Dockerfile changes need `--build`. The `api` image does not mount source: rebuild it (`docker compose build api`) before running scripts in it.
- **Never edit an applied migration.** Add `0005`... (head is `0004`; `/readyz` and a test pin the head).
- **Sabotage checks only on a committed tree.** My `git checkout -- file` restore wiped uncommitted fixes in phase 3. Commit first, then break, run, restore.
- **Write files with the Write/Edit tools,** or a script that writes a temp file and `os.replace`s it; a script that opens a file with `"w"` and then crashes leaves it empty. Heredocs with quotes or `\` break in Git Bash.
- **Warnings are errors in pytest.** A coroutine you forget to await shows up as a failure in some other test.
- **Middleware order matters** (`app/main.py`): request id outermost, then CSRF, then routing. A bare `curl -X POST` gets 403 `CSRF_FAILED`, not 401 or 404.
- **Time in SQL:** pass `now` in from Python (never `now()` in SQL) so tests can move the clock. `CommandTx.now` is the command's clock; `restamp()` after a lock wait; a pinned `now` is never restamped.
- **asyncpg parameter types:** a bind parameter used only inside a `CASE` is inferred as `text`; cast it.
- **FastAPI:** a dependency argument must not share a name with a path parameter (`key`).
- **Unknown query parameters on `GET /items` are 400** (decision 29). Phase 5 adds `q`.
- **The seed writes `work_items` and `item_events` directly** (decision 7). Application code must use `record_event()`; `tests/unit/test_single_writer.py` enforces it.
- **ESLint is pinned to 9** until `eslint-plugin-jsx-a11y` supports 10.
- **Git identity** in this repo is `kpriyam2005p@gmail.com`; the account email is `k2005priyam@gmail.com`. Unconfirmed which is intended.

## Decisions that shape later phases

Read `docs/ENGINEERING_DECISIONS.md` 23-34 before phase 4. In short:

- `command_tx` is one attempt, `run_command` retries; the work function must be safe to run again (24).
- One outbox row and one NOTIFY **per event**, payload `{event_ids: [id], request_id}` (25; SPEC 6.4 and 9 updated).
- A PATCH writes one `field_changed` for the plain fields plus separate `priority_changed`, `confidential_changed`, `requires_approval_changed`, all at the new version; no-op edits write nothing (26).
- **Material edits are refused with 409 while an approval is on record; phase 4 replaces that with invalidation** (27).
- Create body, list parameters, cursors and facets semantics (28, 29); `next_step` is complete (30); idempotency details (31).
- Pages are cut before they are decorated; the admin unfiltered list still sorts the whole table (32).
- **Open for phase 10:** whether a replayed 412 under a reused key is acceptable (decision 34). Until decided, the web client must create a new key for every changed request, including each rebase.

## Open items for the next phases

**Phase 4 (workflow, ownership, approvals)** (BUILD_PLAN Phase 4 is updated)
- **A PATCH of a material field invalidates a pending or approved approval in the same transaction** (SPEC 4.4), replacing decision 27: remove the refusal in `workflow.plan_edit` (`has_live_approval`), restore `edit_text`/`edit_type` in `allowed_actions` (`services/items.py` `_available`), and update `test_editing_the_text_of_an_item_with_an_approval_is_refused_until_phase_4` and the allowed-actions assertions.
- **Replace the membership refusal with the automatic unassign** of SPEC 4.3b (`app/services/teams.py` `_refuse_if_owns_open_items`, decision 14); the tests in `tests/integration/test_teams_members.py` that expect 409 change. It runs inside `run_command` already and must call `record_event`.
- New commands use `run_idempotent` + `run_command`, `lock_by_key` first, `record_event` for every event, and `workflow.py` for validity. `allowed_actions` is policy and workflow merged (`build_view`). `policy.can()` stays about people.
- Lock order: item first, then approvals (SPEC 6.4). The `approvals_item_idx` index (migration 0004) exists for the newest-approval and "live approval" lookups.
- Add the SPEC 13.1 test that replays a seeded sample through `workflow.py`.
- Sabotage rows of BUILD_PLAN Phase 4 go in `docs/TESTING.md`. Remember: commit before sabotaging.

**Phase 5**
- `GET /stats/teams` and `GET /decisions` are **not** built here; they moved to phase 11 (BUILD_PLAN updated).
- The search endpoint (`q` on `GET /items`) joins the HTTP confidentiality test (`test_items_create_read.py`) with attention and notifications; remove `q` from the unknown-parameter refusal.
- Comments write a `commented` event with `item_version` = the current version through `record_event` (it never bumps); the `record_event` version check already enforces that the row and the event agree.
- `unread_since_event_id` already reads `item_reads`; phase 5 adds the upsert (`POST /items/{key}/read`).
- `GET /items/similar` must be declared before `/items/{key}`.

**Phase 6**
- Handlers consume `item.event` rows with `payload.event_ids` (a list of one). The cleanup job deletes idempotency keys older than 24 h (KNOWN_LIMITATIONS).

**Phase 8-10**
- Generated client: operation ids are stable function names. The client sends an `Idempotency-Key` with every mutation and a **new key for every changed request** (rebases included; decision 34).

**Phase 11**
- Builds `GET /stats/teams` and `GET /decisions` first, through `visibility_clause()`, with query-count guards and the T-VIS property test extended.

**Phase 12**
- Expression indexes for `COALESCE(due_at, 'infinity')` if EXPLAIN on `baton_large` shows the sort (decisions 10 and 32); `scripts/bench.py` against `baton_large`.

## Next phase

Phase 4: workflow, ownership and approvals (SPEC 4 and 6.1): the transition table in `workflow.py`, atomic claim, assign/release/unassign, transitions, transfer, approvals with content-bound hashes, and the invalidation that replaces decision 27.
