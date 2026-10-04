# Handoff

Where the project stands, how to run it, and what to know before the next session.

## Current state

**Submission-final (final pass) is done: every planned phase and the frontend extras are on `main`.** Phases 0-7, 10, 11 and 14 (the lean ones are marked in KNOWN_LIMITATIONS), frontend sessions A and B, and branch `fe-extras` merged without conflicts: the command palette (`features/palette`, cmdk), the admin Jobs screen (`features/jobs`), team settings (`features/teams`), inline editors for title, due date, type, confidential and requires-approval (`features/item/FieldEditors.tsx`, `useFieldEdit.ts`) and the 375 px layout (`app/shell/MobileNav.tsx`). Live updates are SSE (phase 7, decision 52). Final-pass changes: `cn()` is `extendTailwindMerge`d with Baton's sizes and weights (test fails on the old one), `POST /items/{key}/read` also marks that item's notifications read (so opening an item from anywhere clears the bell), `web/e2e/axe.spec.ts` (no serious or critical axe findings on login, inbox, queue, item detail, dashboard), CLAUDE.md stack and command lines corrected, DEMO_SCRIPT run literally with two browsers and its wording fixed, README screenshots in `docs/screenshots/`.

Verified from a fresh clone of GitHub (`C:/Projects/baton-clean-check`, `.env` copied from `.env.example` plus `WEB_PORT=8082` because an unrelated container holds 8080 and the dev stack holds 8081): `docker compose up --build`, readyz at migration 0004, sign in as Asha, create and claim an item in the browser, `web-test` (79 passed), `api-test` (3,382 passed, 0 skipped, 609 s). Playwright against the dev stack: 9 of 9 on the last full run (claim race, stale edit, double submit, live update, five axe checks); one earlier full run had two timing failures (double submit, the live update "within 2 s" check) while the clean-clone stack was also busy, and both passed alone and on rerun, so treat them as load-sensitive. Not done: a rehearsal by Kumar, the Mermaid diagrams were not rendered, the Playwright suite is not in CI, `bench.py` was not run. Tags: `submission-candidate-1` (earlier) and `submission-final`. The repo is private; making it public is the owner's decision.

**Earlier state, phase 11 (lean, management visibility) on top of phase 14.** `GET /stats/teams` and `GET /decisions`
(`repo/stats.py`, `repo/decisions.py`, `api/routers/overview.py`, `api/schemas/overview.py`; decision 51), the
`/dashboard` and `/decisions` screens, and the notification bell in the rail (`features/dashboard`,
`features/decisions`, `features/notifications`, `lib/notifications.ts`). Tests: `test_stats.py`,
`test_decisions.py` (full API suite at the end: 3,366 passed, 0 skipped, 469 s; 6 of them new: a member vs a lead on a confidential item, figures equal the same person's
queue lists, paging and filters, 401) and `lib/notifications.test.ts`. Screenshots: `node scripts/management-screens.mjs`
(`THEME=dark`, `BASE_URL`, `PW_CHANNEL=chrome`). Limits: KNOWN_LIMITATIONS "Phase 11 was lean". Tag: `phase-11-done`.
Not built then (the first three were built in fe-extras): team settings, jobs page, command palette; still not built: median age and the 14-day series.

**Phase 14 (lean, submission readiness) is done on top of frontend session B.** README, Top 5 decisions, ARCHITECTURE, TESTING (CB1-CB8 map and the not-tested list), KNOWN_LIMITATIONS (ordered), DEMO_SCRIPT (5 minutes), SPEC marked where it describes unbuilt things (decision 50). CI web lint fixed (`web/eslint.config.js`). Verified from a fresh clone of GitHub: build, migrate, seed, sign in, create and claim an item through nginx, readyz at migration 0004, API test command (3,360 passed, 0 skipped, 360 s; REQUIREMENTS_TRACE D2). Port 8080 is held by an unrelated container on this machine, so the check ran with `WEB_PORT=8082`. **Not done:** a rehearsed demo, PERFORMANCE.md, prepared answers to the six Final Discussion questions, the Mermaid diagrams were not rendered. The repo is private; making it public is the owner's decision. Tag: `submission-candidate-1`.

**Frontend session B (phase 10, lean) is done on top of session A (phase 7, SSE, was built afterwards).** Tags: `phase-4-done`, `phase-6-done`, `fe-a-done`, `fe-b-done`.

**Frontend session B added** (decision 49; limits in KNOWN_LIMITATIONS "Frontend session B"). `web/src/features/item/`: `ItemDetail` (the right-hand pane and `/items/:key`; `Loaded` is keyed by item), `HandoffTrack` + `track.ts` (pure builder), `ActionBar` + `actions.ts` (labels, forms and primary from `next_step`; buttons only from `allowed_actions`) + `useItemActions` (one `useCommand` for every workflow action), `Properties` (optimistic priority and watch), `Description` (local draft in localStorage, banner, `ConflictDialog`), `useSaveFields` (PATCH + `lib/rebase.ts`), `ApprovalPanel` (+ the stale-approve review), `Timeline` (filters, unread divider, optimistic composer), `names.ts`. `lib/`: `rebase.ts`, `eventText.ts`, `useLiveUpdates.ts` (10 s polling, hidden-tab pause, SSE-shaped interface), `announce.ts` (polite and assertive regions in the shell). `components/ui`: `Popover`, `DiffView`, `PriorityGlyph`, `Markdown`. `itemQuery` now merges by `(version, last_event_id)`. Tests: 38 Vitest (18 new: `rebase`, `Description` 412 flow, `actions`) and 3 Playwright (`create-double-submit`, `detail-concurrency`: claim race, stale edit). Screenshots: `node scripts/detail-screens.mjs` (`KEYS="PAY-88:new,..."`, `THEME=dark`). **Not built:** SSE, inline editing of title/type/due/confidential/requires-approval, notifications/dashboard/decisions/teams/jobs screens, command palette, axe, 375 px.

**Frontend session A added** (decision 48; limits in KNOWN_LIMITATIONS "Frontend session A"). `web/src`: `styles/tokens.css` (both themes, avatar hues) + Tailwind v4 mapping in `globals.css`; `lib/api.ts` (ApiError from problem+json, CSRF on unsafe methods and one retry on `CSRF_FAILED`, 401 handler), `api/generated.ts` (from OpenAPI), `api/queries.ts` and `keys.ts`; `lib/itemCache.ts` (`mergeItem`, `upsertItem`, `removeItem`), `lib/useCommand.ts`, `lib/filters.ts` (zod URL filters), `lib/errors.ts` (SPEC 12 copy); `app/` (router, providers, shell with rail, top bar, shortcuts: `j k Enter Esc / c g-i g-q`); `features/auth` (login with demo accounts), `inbox`, `queue` (virtualised list, filter bar with facet counts, search, detail stand-in, "Assign to me"), `create` (New request dialog); `components/ui` (Strip, Button, Field, Menu, Dialog, states, avatar). Tests: 20 Vitest (`itemCache`, `useCommand`, `filters`) and `web/e2e/create-double-submit.spec.ts`. Checked through nginx by hand (see NOTES). **Not built:** `/dev/ui`, command palette, dashboard, decisions, teams, jobs, notifications screens, SSE, axe.

**Phase 6 added** (decision 47): `python -m app.worker` runs the outbox runner (`app/worker/runner.py`: SPEC 9 claim with a 60 s lease, backoff + jitter, dead after 8, SIGTERM-safe) and the SLA sweep every 60 s (`schedules.py`, advisory lock). Handlers: `notify` (`item.event`: requester, assignee, watchers, minus the actor, only `can_view` users), `duplicates` (`item.created`: `item_similar` + one `duplicate_suggested`). `create_item` now enqueues `item.created`. `GET /admin/jobs?status=` and `POST /admin/jobs/{id}/retry`. Compose already scales the worker (`docker compose up --scale worker=2`; checked: both start, one sweep, clean stop). Tests: `tests/integration/test_worker.py` (8: T-OUTBOX 500 jobs two runners, lease takeover, hung handler cut off, backoff to dead + admin retry, double delivery, confidential watcher, duplicates, SLA twice). **Not built:** cleanup job, `FAULT_NOTIFY_FAIL_RATE` (KNOWN_LIMITATIONS, "Phase 6 was lean").

**Phase 5 added** (decision 46): `POST /items/{key}/comments` (key required, `commented` event, author auto-watches, answers `{comment, item}`); `PUT|DELETE /items/{key}/watch`; `POST /items/{key}/read` (upsert of `item_reads.last_read_event_id`); `GET /me/attention` (five sections, 10 items, counts capped at 100); `GET /me/notifications` and `POST /me/notifications/read`; `q` on `GET /items` and `/items/facets` (key jump, else full-text + trigram, top 50, no cursor); `GET /items/similar?team_id&title`; `comment_body` on timeline events; `watching` on the item; `id` on `GET /teams`. New code: `repo/{collab,search,attention,notifications}.py`, `services/comments.py`, `api/routers/collab.py` and `me.py`. Tests: `test_collaboration.py`, `test_search.py`, `test_attention.py`, `test_notifications.py`, and the phase 3 confidentiality test extended. **Not built:** the worker that creates notifications (phase 6), stats and decisions (phase 11). Untested by design: `docs/KNOWN_LIMITATIONS.md`, "Phase 5 was time-boxed".

Repo: https://github.com/KumarPriyam123/baton (private). CI on `main` at `submission-final`: see KNOWN_LIMITATIONS, CI status

What exists, on top of phases 0-3:

- **Workflow rules** (`app/domain/workflow.py`, pure): the SPEC 4.1 table as data (`TRANSITIONS`, one row per action and starting status), `evaluate` (state + payload: reasons, resolutions, duplicates, the approval gate of resolve), `available` and `allowed_actions` (= policy ∩ workflow), `plan_transition`, `plan_transfer`, `plan_removal_unassign`, `plan_edit` with approval invalidation, `claim_plan`.
- **Commands** (`app/services/commands.py`): claim, release, assign (null unassigns), transition (block, unblock, resolve, reopen, close, withdraw), transfer, request/decide/cancel approval, and `unassign_owned_items` for membership changes. One shape: lock, policy, approvals, pending conflict, `If-Match`, workflow, write, `record_event` per planned event.
- **Endpoints** (`app/api/routers/item_commands.py`): `POST /items/{key}/claim | release | assign | transition | transfer | approvals`, `.../approvals/{id}/decision | cancel`. All take an optional `Idempotency-Key`; the versioned ones require `If-Match`. `ALREADY_CLAIMED` carries `claimed_by` and `claimed_at`.
- **Approvals** (`app/repo/approvals.py`): bound to the content by `subject_hash`; a material PATCH or a transfer invalidates every live approval in the same transaction; four-eyes is policy and the CHECK `approvals_four_eyes`.
- **Membership** (`app/services/teams.py`): removing or demoting to viewer unassigns the person's open items in the same transaction (decision 14 is superseded).
- **Tests:** at phase 4, 3,325 API tests and 4 web tests; at submission-final 3,382 passed, 0 skipped, 609 s, 79 web tests, 9 Playwright checks. Phase 4 added T-FLOW (720 cells), T-CLAIM (service and HTTP), T-APPROVE-RACE (50 rounds), the approval and membership races, the seed replay through `workflow.evaluate`, persona checks and per-clause UPDATE guards. Sabotage results: `docs/TESTING.md`.

Verified in this phase: every Done-when item (see `docs/REQUIREMENTS_TRACE.md`), the five sabotage rows (and why two of them needed more tests), the real stack through nginx on port 8081 (migration head 0004, claim, 409, 422 `APPROVAL_REQUIRED`, request, approve, events), an independent review (decision 45: four findings fixed, the rest left with reasons).
**Still deferred:** `docker compose up` on port 8080 itself (another local project holds it); re-check at the phase 14 clean-clone run.

(As of phase 4.) Not built then, by design: the worker, SSE, the web UI, stats and decisions endpoints; all of them exist now.

## Commands

| Task | Command |
|---|---|
| Start everything | `docker compose up --build` (http://localhost:8080; if taken, `WEB_PORT=8081` in `.env` or the shell) |
| API tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` (about 165 s) |
| One test file | `... run --rm api-test pytest tests/integration/test_items_patch.py -q` |
| Web unit tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test` (79 tests, about 50 s; add `--build` after dependency changes) |
| Playwright (host) | stack up, then `cd web && BASE_URL=http://localhost:8081 PW_CHANNEL=chrome npx playwright test` (omit `PW_CHANNEL` to use Playwright's own browser after `npx playwright install chromium`); `... playwright test axe` for the accessibility checks only. After a merge that changes `package.json`, run `npm ci` in `web/` first |
| Screenshots for design review | `cd web && BASE_URL=http://localhost:8081 PW_CHANNEL=chrome node scripts/screenshots.mjs` (`AS="Asha Rao"`, `THEME=dark`); output in `web/test-results/screens` |
| Regenerate API types | stack up, then `cd web && OPENAPI_URL=http://localhost:8081/api/openapi.json npm run gen:api` |
| Web checks on the host | `cd web && npx tsc --noEmit && npx eslint . && npx prettier --check .` |
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
- **Python edit scripts on Windows write CRLF and use the ANSI code page** unless you pass `encoding="utf-8", newline=""`. Prettier fixes the line endings; a `§` can be mangled. Prefer the Edit tool.
- **Web: `npm run gen:api` is a Node script** because npm uses cmd.exe on Windows. Strips are a container-query grid (`.strip-link` in `globals.css`); change the layout there, not per screen.
- **Write files with the Write/Edit tools,** or a script that writes a temp file and `os.replace`s it; a script that opens a file with `"w"` and then crashes leaves it empty. Heredocs with quotes or `\` break in Git Bash.
- **Warnings are errors in pytest.** A coroutine you forget to await shows up as a failure in some other test.
- **Middleware order matters** (`app/main.py`): request id outermost, then CSRF, then routing. A bare `curl -X POST` gets 403 `CSRF_FAILED`, not 401 or 404.
- **Time in SQL:** pass `now` in from Python (never `now()` in SQL) so tests can move the clock. `CommandTx.now` is the command's clock; `restamp()` after a lock wait; a pinned `now` is never restamped.
- **asyncpg parameter types:** a bind parameter used only inside a `CASE` is inferred as `text`; cast it.
- **FastAPI:** a dependency argument must not share a name with a path parameter (`key`).
- **Unknown query parameters on `GET /items` are 400** (decision 29). `q` is now allowed, but not together with `sort` or `cursor`.
- **Tests that read notifications must clear them first:** the seed already fans notifications out to its demo users (`clear()` in `test_notifications.py`).
- **Python on Windows writes CRLF** when a script opens a file in text mode; use `newline=''` (or the Write/Edit tools) or run `sed -i 's/\r$//'`.
- **The seed writes `work_items` and `item_events` directly** (decision 7). Application code must use `record_event()`; `tests/unit/test_single_writer.py` enforces it.
- **ESLint is pinned to 9** until `eslint-plugin-jsx-a11y` supports 10.
- **Never run two test sessions at once.** `tests/db` rebuilds the `public` schema of the shared test database at session start; a second run (or a subagent running tests while you do) can drop it under the first.
- **A test that opens an asyncpg connection must close it** (`try/finally`, or `rows()` / `scalar()` in `tests/support/items.py`). Warnings are errors, so a leaked connection fails some other test later (`ResourceWarning: unclosed transport`).
- **Race tests need distinct people.** `tests/support/workflow.py` has `extra_workers` (new PAY members with the shared test password), `many_clients` (one app, one signed-in client per email) and `released_together` lives in `tests/concurrency/test_workflow_concurrency.py`.
- **Sabotage on a scratch branch from a committed tree**, restoring with `git checkout -- <file>`; the helper pattern is a tiny script that asserts the text occurs once before replacing it.
- **Git identity:** all 140-odd commits are authored as `KPriYam-123 <kpriyam2005p@gmail.com>` (repo-local `user.name` and `user.email`), and the remote is `github.com/KumarPriyam123/baton`. Whether that email is added to the GitHub account (so the commits link to the profile) was not checked: GitHub, Settings, Emails shows it. The commit history was not rewritten.

## Decisions that shape later phases

Read `docs/ENGINEERING_DECISIONS.md` 35-45 before phase 5. In short:

- Order of checks in every command: not visible 404 → policy 403 → (request_approval only) pending approval 409 → `If-Match` 412 → workflow 409/422/400 (35).
- Decision events always carry a reason: the note, or "Approved" / "Closed after resolution" (36).
- A material edit or a transfer invalidates every live approval, not only the latest (37). A transfer answers with the item even if the lead can no longer see it (38).
- The claim UPDATE's WHERE is the only judge of claim state; two of its clauses are redundant for the race and tested one by one (39, 41). `approve` has no second hash check on purpose (40).
- New owners are checked against their membership row under a share lock (42). `Resolve` is offered without an approval and answers 422 (43). Unassign on membership change is batched; deactivation has no endpoint yet (44).
- Still open for phase 10: whether a replayed 412 under a reused key is acceptable (34). The web client must create a new key for every changed request, including each rebase.

## Open items for the next phases

**Phase 5**
- `GET /stats/teams` and `GET /decisions` are **not** built here; they moved to phase 11 (BUILD_PLAN updated).
- The search endpoint (`q` on `GET /items`) joins the HTTP confidentiality test (`test_items_create_read.py`) with attention and notifications; remove `q` from the unknown-parameter refusal.
- Comments write a `commented` event with `item_version` = the current version through `record_event` (it never bumps); the `record_event` version check already enforces that the row and the event agree.
- `unread_since_event_id` already reads `item_reads`; phase 5 adds the upsert (`POST /items/{key}/read`).
- `GET /items/similar` must be declared before `/items/{key}`.

**Phase 6 (left over)**
- The cleanup job (idempotency keys > 24 h, expired sessions, `done` outbox rows > 7 days) and `FAULT_NOTIFY_FAIL_RATE` were skipped on purpose.
- Phase 7 listens on `LISTEN notifications` (payload `{user_id}`) and `item_changes`; `notify_user` and `record_event` already send them.

**Phase 8-10**
- Generated client: operation ids are stable function names. The client sends an `Idempotency-Key` with every mutation and a **new key for every changed request** (rebases included; decision 34).

**Phase 11**
- Builds `GET /stats/teams` and `GET /decisions` first, through `visibility_clause()`, with query-count guards and the T-VIS property test extended.

**Phase 12**
- Expression indexes for `COALESCE(due_at, 'infinity')` if EXPLAIN on `baton_large` shows the sort (decisions 10 and 32); `scripts/bench.py` against `baton_large`.

## Next

Nothing is planned. If there is time, KNOWN_LIMITATIONS has "With another week, in this order": the two security findings (M1 duplicate-suggestion leak, M2 CSP), exact SSE replay and the "N items changed" bar, idempotency on the member endpoints, the cleanup job, admin-path performance, then the test gaps. The old notes above (phase 5-11 "open items") are history; the live list is KNOWN_LIMITATIONS.
