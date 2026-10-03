# Requirements trace

Every requirement in the Newtonite brief, where Baton meets it, the phase that builds it, and the evidence that proves it. Claude Code ticks a row only when the proof exists and passes.

Status: ☐ not started · ◐ built, proof pending · ☑ proven

## Phase 0: foundation (no brief rows of its own; it carries G10, D1-D3, D5)

| Done-when | Status | Proof |
|---|---|---|
| `docker compose up --build` serves the placeholder and `/api/v1/healthz` returns `ok` through nginx | ☑ on 8081 and 8082 · 8080 itself deferred to the phase 14 clean-clone check | `curl` through nginx on 8081/8082 (200, `{"status":"ok"}`, `x-request-id`); Chrome screenshot "API status: ok"; another local project holds 8080 (`WEB_PORT` overrides the host port) |
| `api-test` command passes a trivial test | ☑ | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test`: 20 passed, 0 skipped; also from a fresh clone and in CI |
| `TEST_DATABASE_URL` unset makes pytest fail, not skip | ☑ | exit 2 with the message, run in the container with it empty; `test_pytest_fails_instead_of_skipping_when_test_database_url_is_unset` |
| CI green on GitHub | ☑ | https://github.com/KumarPriyam123/baton/actions/runs/37122809241 (api, web, hygiene all success) |
| LF everywhere, gitleaks clean, PDF and `.env` untracked | ☑ | `git ls-files --eol` has no CRLF; gitleaks scanned 12 commits locally and in CI, no leaks; CI `hygiene` job fails on CRLF, tracked PDF or `.env` |
| `X-Request-ID` on every response, same id in the API log line | ☑ | `test_same_request_id_appears_in_the_api_log_line`, `test_unhandled_exception_returns_problem_json_with_request_id`; manual check through nginx |

## Phase 1: schema, migrations, seed data (carries G1, G7, T1, C3, X1, D5)

| Done-when | Status | Proof |
|---|---|---|
| Constraint tests pass, and each fails if its guard is dropped | ☑ | `tests/db/test_constraints.py`: 28 violations, each rejected under its own guard name (`test_database_rejects_it_and_names_the_guard`) and each succeeding once the guard is dropped (`test_it_only_fails_because_of_that_guard`). By hand: `owner_when_active` removed from the migration, 5 tests red (TESTING.md). |
| Migration round trip is clean | ☑ | `tests/db/test_migrations.py`: catalog snapshot equal after up, down, up; downgrade leaves only `alembic_version`; 0002 converts data both ways. |
| Demo seed under 30 s via `docker compose up`; large seed under 5 min | ☑ | From `down -v`: stack up in 21 s including builds, seed 1.37 s. Large: 69 s wall (`seed_done` 61 s). Idempotent: a second `up` logs `seed_skipped`. |
| 20 random seeded items have valid histories | ☑ | `seed` verifies 20 after loading; `verify_seed` replayed all 600 demo and all 50,000 large histories: 0 problems (17 s for the large set). `tests/unit/test_seed_histories.py`: 6 seeds x 600 histories, 13 kinds of corruption reported. |

## Phase 2: identity, sessions, authorization, visibility (carries T1-T4, G4, G6, CB6)

| Done-when | Status | Proof |
|---|---|---|
| Every cell of SPEC 5.2 has a passing test | ☑ | `tests/unit/test_policy_matrix.py`: 11 scenarios x 25 actions x 9 personas (2,475 cells) typed by hand from the SPEC, plus team, organisation and combined-role rows. Hand sabotage: viewers allowed to claim turned 24 cells red. |
| T-VIS parity passes for all demo users | ☑ | `tests/db/test_visibility_parity.py`: SQL clause equals `can_view` for all 24 demo users and 300 random role combinations over 600 items; counts agree; `LIMIT` applies after filtering; a clause that forgets confidentiality is detected. |
| A confidential Compliance item is 404 to a non-lead member and in no list | ☑ policy and SQL (phase 2), HTTP (phase 3) | `can()` returns `NOT_FOUND` and the clause excludes the item for farah (5+ hidden items, in lists and counts). There is no item endpoint yet; the HTTP-level test is a phase 3 Done-when. |
| The CSRF test covers every unsafe route in OpenAPI | ☑ | `tests/integration/test_csrf.py` reads `/api/openapi.json`; a route added inside the test is protected without touching the middleware. Hand sabotage (check returns True): 6 tests red. |

Also proven: sessions (hash only stored, idle expiry, deactivation and role changes apply at once, logout kills the token), throttling (6th attempt 429, same answer for unknown email), every SPEC 12 code, membership rules, `/readyz`. CI: https://github.com/KumarPriyam123/baton/actions/runs/37130453803 (2,854 API + 4 web tests).

## Phase 3: work items core (carries CB2, CB3, K3, K4, X2, C3, G2, G6, G7)

| Done-when | Status | Proof |
|---|---|---|
| All listed tests pass | ☑ | **T-IDEM** `tests/concurrency/test_items_concurrency.py::test_ten_concurrent_creates_with_one_key_make_exactly_one_item_and_ten_equal_answers` (1 item, 10 equal bodies, 9 `Idempotent-Replayed`), different body 422, domain 4xx replayed, forced 5xx and BUSY not stored. **T-STALE** `::test_two_edits_on_the_same_version_at_once_one_wins_the_other_gets_412` (6 rounds; `changes_since` is exactly the winner's event), `::test_many_edits_on_one_version_at_once_exactly_one_wins`. 428: `test_items_patch.py::test_patch_without_if_match_is_428`. Event invariant: `::test_events_over_a_series_of_edits_have_contiguous_versions`, `::test_a_failed_edit_writes_no_event_and_no_outbox_row`. Pagination over 1,000 items, 4 sorts x 3 limits, ~20% null `due_at`, heavy ties: `test_items_list.py::test_walking_every_page_returns_each_visible_item_once_in_order_for_every_sort`. Filters: 16 cases x 3 people against hand-written SQL. Query count: `test_query_budget.py` (list 5 rows = 100 rows = filtered, at most 6 statements). |
| HTTP-level confidentiality | ☑ (search joins in phase 5) | `test_items_create_read.py::test_confidential_item_is_404_for_a_non_lead_member_and_absent_from_list_and_facets`: 404 `NOT_FOUND` for a CMP member, 200 for the lead, an admin and the requester; absent from the list; facet totals equal the visible rows. Events follow the same rule (`test_items_events.py`). |
| Membership and login run on `command_tx` | ☑ | `tests/integration/test_commands_use_command_tx.py` counts command transactions for login (success and failure), logout, add, change and remove member; two simultaneous identical adds give 201 and 200. |
| `/api/docs` shows every endpoint with typed models | ☑ | `tests/unit/test_openapi.py`: unique operation ids (function names), every JSON response and request body has a schema, `If-Match` and `Idempotency-Key` documented, 412/428/409 listed on PATCH. Checked live through nginx on 8081. |
| No `OFFSET` | ☑ | `grep -rn "offset(" api/app` finds nothing; `tests/unit/test_single_writer.py` scans for `.offset(` and raw `OFFSET`. |
| Independent review | ☑ | ENGINEERING_DECISIONS 34: 4 findings fixed, 4 left with reasons, test gaps closed. |

CI: https://github.com/KumarPriyam123/baton/actions (see HANDOFF for the run of `phase-3-done`). 3,129 API + 4 web tests, none skipped. EXPLAIN on the large seed: ENGINEERING_DECISIONS 32.

## Phase 4: workflow, ownership, approvals (carries CB1, CB4, CB5, K1, T3 four-eyes, E4, E7)

| Done-when | Status | Proof |
|---|---|---|
| Removing or demoting a member unassigns their open items in the same transaction (replaces decision 14) | ☑ | `tests/integration/test_membership_unassigns.py`: every open state goes back to `new` with `unassigned` + `status_changed` at one new version, a waiting approval is cancelled first with the same reason, resolved items and other teams are untouched, a failure after the unassign rolls everything back, demoting a lead to member unassigns nothing. Races: `tests/concurrency/test_workflow_concurrency.py::test_assigning_someone_while_they_are_being_removed...`, `::test_claims_by_a_member_being_removed...` |
| A PATCH of a material field invalidates a pending or approved approval in the same transaction (replaces decision 27) | ☑ | `test_items_patch.py::test_editing_the_text_of_an_item_with_an_approval_invalidates_it_in_the_same_command` (one version bump, `field_changed` + `approval_invalidated` at it), `test_item_approvals.py` (waiting request, approved request, type change), `tests/unit/test_workflow_edit.py` (every stale approval, not only the latest) |
| All tests pass, and every sabotage row was observed red | ☑ | 3,325 API + 4 web tests, none skipped. Sabotage table in `docs/TESTING.md`: all five rows observed red; two clauses needed their own tests because T-CLAIM and T-STALE stay green without them (decision 41) |
| `GET /items/{key}` has correct `allowed_actions` and `next_step` for each demo persona | ☑ | `tests/integration/test_item_personas.py`: ten personas on a sample of seeded Payments items, compared with the hand-typed SPEC table; `next_step` for the approver, a viewer and a new P0 item |
| Independent review run, confirmed findings fixed | ☑ | ENGINEERING_DECISIONS 45: a release that 404ed and rolled back, a lock query that hid a transferred item, a leaked `duplicate_of`, a stale approval requester; fixed with tests that fail on the old code |

Plan tests: **T-FLOW** `tests/unit/test_workflow_flow.py` (15 actions x 6 statuses x 8 relationships, plus the table equals the seed simulator's copy). **T-CLAIM** `tests/concurrency/test_workflow_concurrency.py::test_claim_twenty_concurrent_requests_exactly_one_wins` (20 connections, `asyncio.Barrier`; one winner, nineteen `ALREADY_CLAIMED`, one `assigned` event, version + 1) and the HTTP twin. **T-APPROVE-RACE** `::test_approve_racing_an_edit_never_leaves_an_approval_on_content_nobody_reviewed` (50 rounds). `APPROVAL_REQUIRED`: `test_item_transitions.py`. Four-eyes: policy 403 and, with policy bypassed, the CHECK (`test_item_approvals.py`). Two requests at once: one 201, one 409 (`::test_two_requests_for_approval_at_once...`). Transfer: `test_item_transfer.py`. Release while awaiting approval: `test_item_ownership.py`. Seeded histories replay through `workflow.evaluate`: `tests/unit/test_seed_replay_workflow.py`.

Real stack through nginx on port 8081 (migration head 0004): claim, a losing claim (409 with owner), resolve without approval (422), request, approve, events.

## The situation (pain points the product must fix)

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| S1 | Requests involve several people | single owner + requester + watchers + comments; notifications to all of them | 3, 5, 6 | timeline shows every participant; T-OUTBOX notify tests | ☐ |
| S2 | Some are urgent | priority P0–P3, SLA due dates, overdue sweep, attention sections | 3, 5, 6 | attention tests; SLA sweep test | ☐ |
| S3 | Some require approval before they can move forward | approval gate on resolve, four-eyes, content-bound approvals | 4 | T-FLOW resolve rows; T-APPROVE-RACE | ☐ |
| S4 | Ownership changes several times | claim / release / assign / transfer, each an event; handoff track | 4, 10 | event tests; HandoffTrack screenshot | ☐ |
| S5 | Two people start on the same issue without realising | atomic claim; duplicate suggestions while typing and after creation | 4, 5, 6, 9 | T-CLAIM; similar-items test; E2E claim race | ☐ |
| S6 | Someone changes a request while another views or edits an older version | versions + If-Match + `changes_since`; live updates; conflict dialog | 3, 7, 10 | T-STALE; E2E stale edit | ◐ phase 3: versions, If-Match, `changes_since` proven (T-STALE); live updates and the dialog in 7 and 10 |
| S7 | Important requests disappear in message threads | one queue per team; "Needs an owner"; "going quiet" detection | 5 | attention tests | ☐ |
| S8 | Management can't see what's happening, who owns it, what needs attention, what changed, what's forgotten, or why a decision was made | dashboard; attention; activity timeline; "updated since you looked"; stale/overdue; decision log with required reasons | 5, 11 | phase 11 (lean): `test_stats.py` and `test_decisions.py` (a member does not see the confidential item's count or decision, a lead does, and each dashboard figure equals the same person's queue list); `web/test-results/screens/dashboard-light.png`, `decisions-light.png`. ◐ no median age or 14-day sparkline, "going quiet" is not a link, no T-VIS property test over the new endpoints | ◐ |

## What the system should enable

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| E1 | Create and manage work items for investigation, action or resolution | six item types with defaults; full lifecycle | 3, 4 | API tests; E2E create | ◐ phases 3-4: create, read, edit, list, and the whole workflow (claim to close, transfer, approvals); UI in 8-10 |
| E2 | Understand what the item is and why it exists | title, description, type, requester, created context | 3, 10 | detail screenshot | ☐ |
| E3 | Its current state and importance | status (shape + word), priority, SLA state | 3, 10 | detail screenshot | ☐ |
| E4 | Who is responsible | owner + team, handoff track | 4, 10 | detail screenshot | ◐ phase 4: owner, claim, assign, release, transfer and the `assigned`/`unassigned`/`transferred` events; UI in 10 |
| E5 | What has happened previously | append-only timeline, decisions with reasons | 3, 5, 10 | event invariant test | ◐ phase 3: every change is an event (`record_event`), timeline endpoint `GET /items/{key}/events`; comments in phase 5 |
| E6 | What requires attention next | `next_step` on every item; Inbox sections | 4, 5, 9 | next_step tests | ◐ phase 3: `next_step` implemented (SPEC 4.5, unit-tested) and returned on every item; Inbox in 5 and 9 |
| E7 | Different users on the same item at about the same time behave sensibly | CB1, CB2, CB3, CB5, CB8 | 3, 4, 10 | concurrency tests; two-user E2E | ◐ phases 3-4: CB1, CB2, CB3, CB5 proven on the server; fe-b: two-browser Playwright claim race and stale description edit pass (disjoint edit is a unit test; approve-after-*description*-edit and live are not Playwright-tested, KNOWN_LIMITATIONS) |

## Teams, identity and access

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| T1 | Multiple teams; a user may be in several with different responsibilities | memberships with a role per team | 1, 2 | policy matrix tests | ◐ phase 1: `memberships`, demo personas hold different roles in different teams; policy tests in phase 2 |
| T2 | Not every user can perform every action | permission matrix SPEC §5.2 | 2, 4 | policy matrix tests | ◐ phase 2: the full matrix is proven in the policy module; endpoints enforce it as they are built (3, 4) |
| T3 | Meaningful authorization model | team roles + resource rules (requester, assignee, confidential, four-eyes) | 2, 4 | policy matrix; T-VIS | ◐ phase 2: policy matrix and T-VIS parity proven; four-eyes proven in phase 4 (policy and the CHECK) |
| T4 | Enforced by the system, not only by hiding UI controls | server policy + SQL visibility clause; 404 for invisible | 2, 5, 11 | T-VIS; E2E viewer direct API 403 | ◐ phase 2: server-side policy, SQL clause, 403 on membership routes, CSRF, sessions; item and list endpoints in 3 and 5 |

## Collaboration and history

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| C1 | Users collaborate around work items | comments, watchers, notifications | 5, 6, 10 | comment tests; E2E | ◐ fe-b: composer with optimistic "Sending…", same key on Retry, event inserted on success (driven by hand; no automated UI test); notifications UI skipped |
| C2 | Understand how an item evolved: responsibility, priority, workflow | typed events with from/to; handoff track; Decisions filter | 3, 4, 10 | event tests; screenshot | ◐ phase 3: typed events with from/to and reasons for decisions; handoff events in phase 4; fe-b: handoff track (log-width segments, dashed unowned, "+N" fold, hover/focus text, sr-only list) and All/Comments/Decisions timeline, reviewed in screenshots |
| C3 | Important actions don't silently disappear | one write path (`record_event`), same transaction, append-only trigger, reasons required for decisions | 1, 3 | event invariant test; trigger test | ◐ phases 1 and 3: append-only trigger; `record_event` writes event, stamp, outbox row and NOTIFY together; single-writer scan; the version check refuses a wrong version; reasons for priority lowering and approval off |

## Concurrent usage

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| K1 | Two users try to take responsibility for the same work | atomic conditional claim | 4 | T-CLAIM; E2E claim race | ◐ phase 4: T-CLAIM proven (service and HTTP); fe-b: Playwright, two contexts released together: one "Assigned to you", the other "took PAY-nnn", both end on one owner (`e2e/detail-concurrency.spec.ts`) |
| K2 | One user views information another just changed | live updates; version guard; stale banner; If-Match on intent-dependent actions | 3, 7, 10 | SSE tests; E2E live update | ◐ phase 3: ETag, version guard on the server side; fe-b: item refetch merges by (version, last_event_id), 10 s polling instead of SSE (a priority change by another user showed after 9 s, driven by hand, no Playwright test), change notice + wash + aria-live, draft banner, stale-approve review; SSE in phase 7 |
| K3 | Multiple updates within a short period | row lock + versions (no lost updates); per-item client mutation queue; event coalescing | 3, 9, 10 | T-STALE; vitest scope test | ◐ phase 3: row lock + versions proven (10 edits on one version: 1 wins); fe-a: `useCommand` `scope: item:<id>` unit-tested (two commands run in order, second with the new `If-Match`); event coalescing in 10 |
| K4 | A user repeats an action, unsure if the first succeeded | idempotency keys in the same transaction; same key on retries | 3, 9 | T-IDEM; E2E double-submit | ◐ phase 3: T-IDEM proven on the server; fe-a: one key per action reused by every retry (unit-tested) and Playwright double-click with the first response dropped gives one item and an `Idempotent-Replayed` answer; item commands other than create and claim are phase 10 |
| K5 | Handle at least some deliberately | all four above | — | TESTING.md | ☐ |

## User experience

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| U1 | Useful overview of ongoing work | Inbox + Dashboard | 9, 11 | phase 11 (lean): screenshots of the dashboard in light and dark and of the decision log and notification popover at 1280 px, looked at against DESIGN 4.6, 4.7, 4.11; not run: 375 px, axe | ◐ |
| U2 | Quickly see what requires attention | attention sections; next-step chips | 5, 9 | attention tests | ◐ fe-a: Inbox sections and next-step chips built and checked in the browser (screenshot reviewed against DESIGN); no Playwright test for it |
| U3 | Find relevant work without browsing everything | filters in URL, facets, full-text + fuzzy search, key jump, command palette | 5, 9 | search tests; E2E | ◐ fe-a: URL filters (zod, round-trip tested), facet counts, sort, search as you type with marked words and typo tolerance, key jump: driven in the browser; command palette skipped |
| U4 | Usable as stored work grows | keyset pagination, virtualised list, indexes, ranked search capped | 3, 9, 12 | PERFORMANCE.md on large seed | ◐ phase 3: keyset pagination over 1,000 items, EXPLAIN on the large seed (decision 32); fe-a: virtualised infinite list built, **not yet run on the large seed**; numbers in 12 |
| U5 | Coherent, responsive experience over decorative UI | DESIGN.md principles; performance budgets; designed states | 8–11 | screenshots; axe; budgets | ☐ |

## System behaviour

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| B1 | Secondary work (notifications, processing, enrichment) may be async | outbox worker: notifications, duplicate detection, SLA sweep, cleanup | 6 | worker tests | ◐ phase 6 (lean): notify, duplicates, SLA sweep, `test_worker.py`; cleanup not built |
| B2 | Consider failure, running more than once, delay | retries with backoff, dead letters + retry UI, idempotent handlers, leases, advisory locks; UI never depends on async | 6, 11, 13 | T-OUTBOX; failure drills | ◐ phase 6: backoff, dead after 8, `/admin/jobs` + retry, lease takeover tested; UI in phase 11 |

## Expected scale

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| X1 | Thousands of users, hundreds to a few thousand simultaneous, many teams, tens of thousands of active items, large growing history | large seed (2,000 users, 40 teams, 50,000 items, ~600,000 events); bench with concurrent users | 1, 12 | PERFORMANCE.md | ◐ phase 1: large seed loads (2,000 users, 40 teams, 50,000 items of which 18,107 open, 586,983 events, 127,714 comments); benchmarks in phase 12 |
| X2 | Must not load the entire dataset into the browser or application memory | keyset pagination everywhere, bounded queries, worker batches, bounded SSE queues | 3, 7, 9 | no-OFFSET check; query-plan test | ◐ phase 3: no OFFSET anywhere (scan + grep), limit capped at 100, query counts do not grow with rows; UI in 9 |
| X3 | Design for continued growth | scaling path in ARCHITECTURE.md (partitioning, replicas, sequences, search engine) | 14 | ARCHITECTURE.md | ☐ |

## Engineering expectations (the ten named areas)

| ID | Area | Where it's shown | Proof | Status |
|---|---|---|---|---|
| G1 | Data modelling | SPEC §3; constraints; event log | schema tests; Decision #5 | ◐ phase 1: schema, 28 named guards, index inventory, drift tests proven; the write-up (Decision #5) in phase 14 |
| G2 | API design | commands vs PATCH, ETag/If-Match, problem+json, idempotency, keyset cursors | OpenAPI; Decision #3 | ◐ phase 3: ETag/If-Match, problem+json, idempotency, keyset cursors, stable operation ids; commands in phase 4 (claim, assign, transition, transfer, approvals; ETag on every answer) |
| G3 | Frontend state management | TanStack Query, version merge, mutation scopes, rebase | vitest suite | ☐ |
| G4 | Authorization | policy + visibility clause | policy matrix; T-VIS | ☑ phase 2 (policy matrix, T-VIS parity; per-endpoint enforcement is tracked under T2-T4) |
| G5 | Concurrent operations | CB1–CB3, CB5 | concurrency tests | ◐ phases 3-4: CB1, CB2, CB3, CB5 proven |
| G6 | Error handling | SPEC §12 mapping; timeouts; retries; designed error states | failure drills | ◐ phases 2 and 3: every SPEC 12 code; lock/statement timeouts become 503 BUSY with Retry-After; deadlock retry; constraint violations become domain errors |
| G7 | Data consistency | single transaction per command; outbox; DB constraints | event invariant; rollback test | ◐ phases 1 and 3: one transaction per command with the idempotency key inside it; outbox row in the same transaction; failed commands leave nothing; worker in 6 |
| G8 | Search and filtering | FTS + trigram + facets + URL filters | search tests | ☐ |
| G9 | Application performance | indexes, budgets, virtualisation | PERFORMANCE.md | ☐ |
| G10 | Maintainability | pure domain modules, layered code, generated client, CI, docs | CI; layout | ◐ phase 0: layout, CI green (run 37122809241), pre-commit; domain modules and client later |
| G11 | Smaller system with well-considered behaviour over many incomplete features | explicit out-of-scope list | KNOWN_LIMITATIONS.md | ☐ |

## Critical behaviour (at least three beyond CRUD)

| ID | Brief example | Baton | Test | Status |
|---|---|---|---|---|
| CB1 | Simultaneous actions by multiple users | atomic claim | T-CLAIM | ◐ phase 4 proven (T-CLAIM); demo in 10 |
| CB2 | Handling stale information | versions + If-Match | T-STALE | ◐ phase 3 proven (T-STALE); demo in 10 |
| CB3 | Preventing accidental duplicate operations | idempotency keys | T-IDEM | ◐ phase 3 proven (T-IDEM); demo in 9 |
| CB4 | Enforcing workflow rules | workflow module + DB constraints | T-FLOW | ◐ phase 4 proven (T-FLOW, `APPROVAL_REQUIRED`); demo in 10 |
| CB5 | (beyond the examples) approvals bound to content | subject hash + invalidation | T-APPROVE-RACE | ◐ phase 4 proven (T-APPROVE-RACE, 50 rounds); demo in 10 |
| CB6 | Authorization at the resource level | confidential, requester, four-eyes, 404 | T-VIS | ◐ phases 2 and 3: SQL and Python agree (T-VIS); list, facets and events checked over HTTP; search, attention and notifications in phase 5 |
| CB7 | Reliable asynchronous processing | outbox + SKIP LOCKED + idempotent handlers | T-OUTBOX | ◐ phase 6: `test_worker.py::test_two_runners_against_500_jobs_do_each_side_effect_exactly_once` |
| CB8 | Reconciling optimistic frontend state with server decisions | version merge, scopes, rebase, rollback | T-RECONCILE + E2E | ◐ fe-b: rebase disjoint vs overlapping, resend limit, ConflictDialog choices send the right request, `allowed_actions` drives the bar (18 Vitest) + 2 Playwright; coalescing test not written (polling, decision 49) |
| CB-P | Be prepared to explain them | DEMO_SCRIPT.md | rehearsal done | ☐ |

## Submission

| ID | Brief asks for | Where | Status |
|---|---|---|---|
| D1 | Working source code | repository (private: github.com/KumarPriyam123/baton) | ◐ phase 0 skeleton pushed |
| D2 | Clear instructions for running the application | README Quick start (verified from a clean clone) | ☑ fresh clone of GitHub at `2d57929`: `cp .env.example .env`, `docker compose up --build` (stopped at "port 8080 is already allocated" because an unrelated container holds it), then the README's fix `WEB_PORT=8082`; sign in as asha, create and claim PAY-113 through nginx, `/readyz` at 0004, `api-test`: 3,360 passed in 360 s, 0 skipped. Sign-in was checked by API (curl), not by clicking in a browser |
| D3 | Any required setup instructions | README; `.env.example` | ◐ `.env.example` lists every variable; README stub |
| D4 | Engineering decisions document (~5 decisions, trade-offs) | `docs/ENGINEERING_DECISIONS.md` | ☑ "Top 5 decisions" table (decision, alternative, trade-off) above the 50-entry log |
| D5 | Automated tests for important behaviour, reflecting the architecture's risks | `api/tests`, `web/src/**/*.test.ts`, `web/e2e`; `docs/TESTING.md` | ◐ phases 0-1: harness, fail-not-skip guard, database guards (253 tests: 249 api + 4 web) |
| D6 | Brief description of known limitations | `docs/KNOWN_LIMITATIONS.md` | ☑ ordered summary table, SPEC 15 list, then the detail |
| D7 | Architecture diagrams or extra docs (welcome) | `docs/ARCHITECTURE.md` | ☑ Mermaid component and sequence diagrams, scaling path (diagrams not rendered in this session: unverified) |
| D8 | Important assumptions documented | README Assumptions (SPEC §1) | ◐ README Assumptions section holds A1-A14 as written in SPEC §1 |

## Final discussion (prepared in DEMO_SCRIPT.md)

| ID | They'll ask | Prepared | Status |
|---|---|---|---|
| F1 | How your architecture works | ARCHITECTURE.md + 2-minute verbal walkthrough | ◐ written; not rehearsed; no PERFORMANCE.md (not measured) |
| F2 | What happens when things fail | failure drills in TESTING.md | ☐ |
| F3 | What assumptions you made | README Assumptions | ◐ written; not rehearsed; no PERFORMANCE.md (not measured) |
| F4 | Which parts you consider most important | CB1–CB8 ranked, with why | ☐ |
| F5 | What you'd change if it grew significantly | ARCHITECTURE.md scaling path; PERFORMANCE.md "what breaks first" | ◐ written; not rehearsed; no PERFORMANCE.md (not measured) |
| F6 | What you'd do with another week | KNOWN_LIMITATIONS.md, ordered | ◐ written; not rehearsed; no PERFORMANCE.md (not measured) |
| F7 | Explain or modify any part live | NOTES_FOR_INTERVIEW.md per phase; practised change requests | ☐ |
