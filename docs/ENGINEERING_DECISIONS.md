# Engineering decisions

## Top 5 decisions

The five choices that shape the system most. Each row says what I chose, what I turned down, and what
that costs. The numbered log below has the detail (the number in brackets).

| # | Decision | Alternative I rejected | Trade-off I accepted |
|---|---|---|---|
| 1 | **PostgreSQL is the database, the queue, the search engine and the pub/sub** (1): outbox + `SKIP LOCKED`, `tsvector` + `pg_trgm`, `LISTEN/NOTIFY` | Redis, Kafka/Celery, Elasticsearch next to Postgres | A queue and a search index in the same database share its ceiling and its load; ranking is simpler than a search engine's. In return a job or an event can never exist without the change that caused it, and there is one thing to run, back up and explain |
| 2 | **Concurrency without long locks** (8, 34, 35, 39-42): an atomic conditional `UPDATE` decides a claim; a `version` + `If-Match` decides an edit; approvals are bound to a hash of the content and a material edit invalidates them | Pessimistic "check out" locks held while someone thinks; last write wins; a lock-then-read-then-write claim | The loser of a race gets a 409/412 and has to look at a diff and choose (the conflict dialog). Edits to one hot item serialise on its row lock. The version moves only for decision-relevant fields, which makes the rule harder to state than "every write" (8) |
| 3 | **Idempotency keys are stored in the same transaction as the change** (31, 34): one key per user action, reused on every retry; a retry replays the first answer | A cache or a separate table written after the commit; client-side de-duplication only | The keys table grows until the cleanup job exists (not built), a replayed 412 can outlive its reason, and every state-changing endpoint carries the extra header and fingerprint check |
| 4 | **Authorization and workflow are pure server-side modules** (`policy.py`, `workflow.py`), mirrored by one SQL `visibility_clause()` and by database CHECK constraints (I3, I4, I10); the UI renders `allowed_actions` and decides nothing; an item you cannot see is a 404, one you cannot touch is a 403 | Permission checks scattered through handlers and the UI; filtering lists in Python; 403 for everything | Every query that returns items or counts must remember the clause (a parity test, T-VIS, guards it), and the same rule exists twice (Python and SQL) and in the database. In return a missing check fails a test, not a customer |
| 5 | **One append-only event log** (`item_events`) is the audit trail, the timeline and the source of the outbox (25): one row per change in the same transaction, a trigger rejects UPDATE/DELETE | Full event sourcing (rebuild state from events); a separate audit table; updating rows in place without history | The row and its event are written together (a dual write inside one transaction, checked by `record_event`) rather than derived from each other; `TRUNCATE` is not covered by the trigger (11); history is large |

## Decision log

Changes to the stack or the spec, recorded when they are agreed.

## 1. Postgres is the queue, the search engine and the pub/sub

Outbox + `SKIP LOCKED`, `tsvector` + `pg_trgm`, and `LISTEN/NOTIFY` replace Redis, Kafka, Celery
and Elasticsearch. One system to run, and the outbox is transactional with the data it describes.

## 2. Phase 0: "refuse to start if DEMO_MODE or any FAULT_* is set" means enabled

`ENV=prod` rejects `DEMO_MODE=true` and `FAULT_NOTIFY_FAIL_RATE>0`. `DEMO_MODE=false` and
`FAULT_NOTIFY_FAIL_RATE=0` are allowed, because `.env.example` lists those variables with those values.
Tested in `api/tests/unit/test_config.py`.

## 3. Phase 0: web host port is `${WEB_PORT:-8080}`

SPEC and CLAUDE.md say web publishes 8080. The default is unchanged; the variable exists because
another local project can hold 8080 (it did). It is still the only published port.

## 4. Phase 0: ESLint is pinned to 9

`eslint-plugin-jsx-a11y` (required by the plan) does not yet support ESLint 10.
Revisit when it does.

## 5. Phase 0: test containers mount the source

`compose run` does not rebuild images, so a stale image would run old code and still look green.
`api-test` mounts `./api`, `web-test` mounts `./web/src`. Changing dependencies still needs `--build`.

## 6. Phase 1: one addition to the listed constraints

`approvals_rejection_has_note`: a rejected approval must have a non-blank `decision_note`. SPEC 3.2
says the note is "required on rejection"; CLAUDE.md I10 wants critical rules in the database too.
Tested in `tests/db/test_constraints.py`.

## 7. Phase 1: the seed writes work_items and item_events directly (exception to I1)

600,000 events cannot go through `record_event()`. The bulk loader (`scripts/seedlib/load.py`)
is the only other writer, runs before the app starts, and every history it produces is replayed and
checked by `scripts/seedlib/verify.py`. Its transition table is its own (`sim.py`); SPEC 13.1's
"replay a sample through `workflow.py`" test needs `workflow.py` and is added in phase 4.

## 8. The version counts decision-relevant state only (decided after phase 1)

`version` increments by 1 per command that changes title, description, type, priority, status,
assignee, team, due_at, confidential, requires_approval, approval state or resolution. Comments,
`duplicate_suggested` and `sla_breached` write an event carrying the current version and never
bump it; watch changes write no event. Reason: a comment must not make every other editor's
`If-Match` stale (412) or force a rebase. Consequences handled in SPEC 6.2, 6.6 and 11:
`changes_since` lists only versioned events; the timeline cursor is `after_event_id`; the client
version guard breaks ties with `last_event_id`; "unread" moves to event ids (decision 12).
System events do not set `last_activity_at` (a machine event must not reset "going stale");
comments do.

## 9. Event `team_id` is the item's team after the change (decided after phase 1)

Every event of a command carries the team the item has when the command ends. For a transfer that
is the new team, including an `approval_cancelled` written in the same command. Reason: one rule,
easy to check, and the decision log of the receiving team shows the transfer that brought the
item to it. The seed verifier checks it at each command boundary. `item_events.team_id` also has a
foreign key to `teams` (migration 0001).

## 10. Phase 1: indexes are exactly the SPEC list; sort-expression question deferred

SPEC 3.2 indexes `due_at` plainly while SPEC 8 sorts and pages on `COALESCE(due_at, 'infinity')`.
A keyset cursor on that expression cannot use the plain index fully. Built as listed; if
`EXPLAIN` in phase 3 or 12 shows a sort, a new migration swaps in expression indexes under the same names.

## 11. Phase 1: TRUNCATE is not covered by the append-only trigger

SPEC 3.2 specifies a trigger on UPDATE and DELETE. `seed --reset` needs TRUNCATE, so it is left
open. Application roles in a real deployment would not be granted TRUNCATE (see KNOWN_LIMITATIONS later).

## 12. "Unread" is tracked by event id, not version (migration 0002)

`item_reads.last_read_version` became `last_read_event_id bigint`. With decision 8 a comment does not
change the version, so a version cannot tell a reader about new comments; an event id can. The
migration renames and widens the column in place and converts existing values ("version V seen"
becomes the newest event with `item_version <= V`; the downgrade maps back). Tested with data in
`tests/db/test_migrations.py`. No foreign key to `item_events` was added: the SPEC lists a plain
bigint, and the seed verifier checks that each marker belongs to its own item.

## 13. Keys and numbers are immutable in the database (migration 0003)

SPEC 3.2 says `teams.key` is immutable and `work_items.key` never changes, but only the
application would have enforced it, which CLAUDE.md I10 does not allow for a critical rule.
`BEFORE UPDATE` triggers reject changes to `teams.key` and to `work_items.key`, `number` and
`origin_team_id` (the three that make a key unique). Rewriting the same value is allowed, and
every other column (item_seq, version, team_id on a transfer...) is untouched.


## 14. Phase 2: removing or demoting a member is refused while they own open items

**Superseded in phase 4:** the commands now unassign the person's open items in the same transaction (SPEC 4.3b); see the end of this file. What follows is the phase 2 reasoning.

SPEC 4.3b wants those items unassigned in the same transaction, which needs `record_event()` and the
workflow (phases 3 and 4). Writing `work_items` from the membership code would break I1, so for now
`DELETE` and "demote to viewer" return 409 `WORKFLOW_VIOLATION` ("They still own N open items...")
and change nothing. Phase 4 replaces the refusal with the automatic unassign. Demoting a lead to
member is allowed (a member can own items). Decided with Kumar.

## 15. Phase 2: METHOD_NOT_ALLOWED added to the error table

405 now has a code in SPEC 12, so a wrong method is problem+json like everything else. No other
code was added; the other statuses reuse the table (wrong password is 401 `UNAUTHENTICATED` with the
detail "Invalid email or password.", a body field that fails is 400 `VALIDATION_FAILED`).

## 16. Phase 2: demo password is a constant in `app/demo.py`, not a setting

Shared by `GET /demo/users` and the seed. No new configuration variable. It is not a secret (the login
page shows it in demo mode) and the API refuses `DEMO_MODE` in `ENV=prod`.

## 17. Phase 2: any signed-in user can list a team's members

SPEC 11 does not restrict `GET /teams/{key}/members`. It follows `/teams` and `/users`, which are
open to every signed-in user so requests can be routed and pickers can work.

## 18. Phase 2: CSRF is checked before routing and before authentication

An unsafe request without the token is 403 `CSRF_FAILED` whether or not the caller is signed in, and
whether or not the route or method exists: a wrong method without the token gets 403, with it 405.
The rule is by HTTP method, so a route added later is covered without anyone remembering.

## 19. Phase 2: sessions

Idle expiry is 12 h from the last use; `last_seen_at` and `expires_at` are written at most once a
minute so a read does not become a write on every request. The browser cookie is a session cookie
(no `Max-Age`); the server enforces the expiry. Password verification runs in a worker thread, and an
unknown email does the same hashing work as a wrong password.

## 20. Phase 2: no `POST /teams` route

SPEC 5.2 lists "create teams" as an admin action, and the policy covers it, but SPEC 11 has no route.
Phase 11's team administration adds it.

## 21. Phase 2: `/readyz` body

200 or 503 with `{status, database, migrations, current, expected}`. It is an infrastructure probe,
so it is not problem+json. `/healthz` stays pure liveness.

## 22. Phase 2: Idempotency-Key is not yet read on membership routes

SPEC 11 marks it optional there. Adding a member is naturally idempotent (same role: 200, no change),
and the idempotency layer arrives in phase 3.

## 23. Phase 3: an index on approvals by item (migration 0004)

SPEC 3.2 lists no index on `approvals` except the partial unique one for pending requests. The item
view shows the newest approval (`ORDER BY requested_at DESC LIMIT 1`) and a command asks whether a
pending or approved one exists; both look up approvals by item. `approvals_item_idx (item_id,
requested_at DESC)` serves both. `tests/db/test_indexes.py` lists it as an addition to the SPEC set.

## 24. Phase 3: `command_tx` is one attempt, `run_command` is the retry

BUILD_PLAN asks for a context manager that also retries the transaction on 40001/40P01, but a context
manager cannot run its own body twice. `command_tx(conn)` starts the transaction (`SET LOCAL
lock_timeout '2s'`, `statement_timeout '5s'`) and turns database errors into domain errors (lock or
statement timeout: 503 BUSY with `Retry-After`; a named constraint: the error in `db/errors.py`).
`run_command(conn, work)` calls it up to 3 times; after the third failure it answers 503 BUSY. Work
must be safe to run again from the start. A test lists every CHECK and partial unique index in the
schema and fails if one is neither mapped nor declared a bug. The session "touch" that
`sessions.authenticate` writes at most once a minute stays outside (it is infrastructure, not a command).

## 25. Phase 3: one outbox row per event, not per command

SPEC 6.4 draws one outbox row for a command; BUILD_PLAN and CLAUDE.md I1 describe `record_event`
writing "the outbox row and NOTIFY" for each change. `record_event` writes one row per event with
payload `{event_ids: [id], request_id}`, so the phase 6 handler (keyed by event id, `UNIQUE (user_id,
event_id)`) is the same either way. NOTIFY is also per event.

## 26. Phase 3: what a PATCH writes

- Permissions are checked for every field the client **sent**, even if the value is unchanged.
  Permission (403) is judged before the version (412), so a stale request from someone who may not
  edit learns nothing.
- One command bumps `version` by exactly 1 and writes one event per kind: `field_changed` (title,
  description, type, manual `due_at`, all in one event), `priority_changed` (with the recomputed
  `due_at` in its data), `confidential_changed` (always a decision; the reason is optional because
  SPEC 4.2 does not require one), `requires_approval_changed` (a decision when turned off; reason
  required then). All carry the new version.
- An edit that changes nothing is 200 with no version, no event and no outbox row.
- `due_at` cannot be null in a PATCH; a lead's date sets `due_source = manual` for good.
- `apply_type_defaults` (with `type`) resets `requires_approval` and `confidential` to the new type's
  defaults; what the lead asked for in the same request wins. The reason rules still apply.
- `changes_since` is capped at 100 events (a bound, not a page).

## 27. Phase 3: material edits are refused while an approval is on record (until phase 4)

**Superseded in phase 4:** a PATCH of a material field invalidates every pending or stale approved approval in the same transaction (decision 37). What follows is the phase 3 reasoning.

SPEC 4.4 says a material edit invalidates approvals. That is phase 4, but seeded data already has
`awaiting_approval` and approved items. Rather than let a title change slip past an approval (SPEC A6),
a PATCH of `title`, `description` or `type` on an item with a pending or approved approval returns 409
`WORKFLOW_VIOLATION`, and `allowed_actions` leaves out `edit_text` and `edit_type`. Phase 4 replaces the
refusal with the invalidation. `requires_approval` cannot change while `awaiting_approval` (SPEC 4.2).

## 28. Phase 3: the create body

`team_key`, `type`, `title`, `description` (default empty), `priority` (default 2), `requires_approval`.
`requires_approval: true` is accepted only for an ops task (SPEC 3.1) or when the type already
requires it; anything else is 400 on that field. There is no `confidential` at creation: it comes
from the type. A missing `Idempotency-Key` is 400 (SPEC 12 has no better row) and the OpenAPI document
marks the header required. An unknown `team_key` is 400 on that field, not 404.

## 29. Phase 3: list parameters

`status[]`, `priority[]` and `type[]` are repeated parameters (`status=new&status=blocked`). Unknown
query parameters are 400 rather than ignored, so a filter that is silently dropped cannot show the
wrong items; `q` is reserved for phase 5 and is refused until then. `overdue` means open and past
`due_at`. `updated_since` is inclusive and needs a time zone. Facets apply all the current filters
(the literal reading of SPEC 8) and list a value only if it has items. Sorts: `updated` and `created`
break ties by `id DESC` to match the DESC indexes. A cursor names its sort and is refused by another.
Items and events: default 50, max 100 (members and users keep 20/50). The timeline is oldest first
by default, `order=desc` for newest first, and `after_event_id` is the cursor in either direction.

## 30. Phase 3: `next_step` is the whole SPEC 4.5 table

BUILD_PLAN says placeholder. A wrong placeholder is worse than a small pure function with unit tests,
so `workflow.next_step` implements every row. The blocked reason is the reason of the newest `blocked`
event, fetched only for blocked items.

## 31. Phase 3: idempotency details

The fingerprint is exactly SPEC 3.2 (method, path, canonical body): `If-Match` is not part of it.
Domain 4xx answers are stored, including 403 and 404 and a replayed 412; BUSY and 5xx are not. Keys
older than 24 h are removed by the phase 6 cleanup job; until it exists, an old key still replays.
Header parameters of the dependencies are named `idempotency_key`, not `key`, which clashes with the
`{key}` path parameter in FastAPI.

## 32. Phase 3: pages are cut before they are decorated; one EXPLAIN finding

The first list query joined teams, users, the newest approval (LATERAL) and the last event for every
row before sorting. On the large seed (50,000 items) that was 20-110 ms. `list_items` now filters,
orders and limits `work_items` in an inner query and joins only the 51 rows of the page (3-12 ms for
leads and members). **Still open for phase 12:** an admin's unfiltered list sorts all of `work_items`
(about 20 ms), and decision 10 (expression indexes for `COALESCE(due_at, 'infinity')`) is the fix.
`facets` is one aggregate query (GROUPING SETS), 9-35 ms.

## 33. Phase 3: operation ids are the route function names

`generate_unique_id_function` returns the function name, so the generated client does not change when
a route moves (FastAPI's default ends in path and method). A test fails if an operation lacks an id,
a typed response or a typed request body.

## 34. Phase 3 review: what an independent reviewer found, and what was decided

A reviewer that had not seen the session read `tx.py`, `idempotency.py` and their tests
(`git diff phase-3-start..HEAD`) and probed them against real Postgres. No critical or high finding.
Fixed (commit "fix(api): findings of the independent review"):

- **NUL or a lone surrogate was a 500** (`reason` had no check; a surrogate broke the fingerprint's
  encoding). One validator (`_no_nul`) now covers title, description and reason; the fingerprint
  encodes with `surrogatepass` as a second line.
- **`tx.now` was taken before waiting for a lock**, so a command that waited stamped `updated_at` and
  event times from before the winner's commit (non-monotonic, which `sort=updated` and
  `updated_since` would trip over). `CommandTx.restamp()` takes the time again once the row lock (or
  the team counter lock) is won, unless the caller pinned `now`.
- **`record_event` did not check that the item it was given had the row's version.** The stamp
  UPDATE now returns `version` and `team_id` and the command rolls back (RuntimeError, 500) on a
  mismatch, so I2 is a runtime invariant for phase 4's many commands, not a convention.
- **A hidden item was locked before visibility was checked**, so a 503 behind someone else's lock
  could reveal that it exists. A PATCH now reads visibility first (404), locks, and judges again.
- Test gaps closed: the retry loop through the idempotency layer plus `record_event`; the
  `DBAPIError` branch (constraint stored as 4xx, lock timeout is BUSY and not stored, an unmapped
  error rolls the key back); a task cancelled mid-command; the single-writer scan now covers method
  forms, aliases, the outbox and NOTIFY.

Not changed:

- **A stored 412 (or 403/404) is replayed under the same key, and `If-Match` is not part of the
  fingerprint.** Reviewer scenario: the client gets a 412 under key K, rebases, and re-sends under the
  same K with a new `If-Match`; the server replays the old 412 for 24 h. SPEC 6.3 says to store domain
  4xx and SPEC 3.2 defines the fingerprint, so this is a SPEC question, not a bug to fix silently.
  Options: (a) never store state-dependent 4xx (412, 403, 404), or (b) put `If-Match` in the
  fingerprint so reuse gets an explicit 422. **Open; needs a decision before phase 10.** Until then the
  rule for the web client is: a rebase or any changed request is a new user action and gets a new key
  (`lib/useCommand.ts`, phase 8/9).
- **Path case in the fingerprint** (`/items/pay-1` vs `/items/PAY-1` hash differently): a retry resends
  identical bytes, so no legitimate retry is affected. Not worth normalising.
- **`request_id` in a replayed error body** is the first request's id. It identifies the request that
  was logged; the replay's own id is in `X-Request-ID`. Left.
- **The actor context is loaded before the command transaction** (SPEC 6.4 draws it inside). A role
  change landing while a command waits up to 2 s for a lock is not seen by that command. Recorded in
  KNOWN_LIMITATIONS; SPEC 5.3 (fresh on every request) holds.

## 35. Phase 4: order of checks in a command, and the approval-request race

Every command judges in this order: not visible (404) → policy (403) → **a pending approval, for
`request_approval` only** (409 `APPROVAL_ALREADY_PENDING`) → `If-Match` (412) → workflow (409/422).
SPEC 6.2 requires `If-Match` on `request_approval`, and the plan's test wants the loser of two
concurrent requests to get 409. Both carry the same version, so after the winner commits the loser's
version is stale; putting the pending check before the version check is the only way to keep both
statements true. A stale `If-Match` with no pending approval is still 412. Decided with Kumar.

## 36. Phase 4: decision events always carry a reason

`approve` and `close` of a resolved item are ★ decisions, but SPEC 4.1 requires no reason for them,
while SPEC 3.2 says decisions carry reasons and `verify_seed` rejects a decision without one. The
note is optional in the API; when it is missing the event's reason is a fixed text ("Approved",
"Closed after resolution"). The user's own words are never replaced. Rejection still requires a note.
Decided with Kumar.

## 37. Phase 4: a material edit or a transfer invalidates every live approval

SPEC 4.4 says "the latest `approved`". The command invalidates **every** pending approval and every
approved one whose `subject_hash` no longer matches the content (the same rule the seed simulator
uses). With two approved rows an older one could otherwise match again after an edit-back, and
T-APPROVE-RACE can assert "every approved row matches the current content". One
`approval_invalidated` event names all of them (`approval_ids`) and the fields that changed.
Decided with Kumar.

## 38. Phase 4: `POST /transfer` answers with the item even if the actor can no longer see it

A lead of the old team who transfers an item away is no longer allowed to see it (SPEC 4.3.4). The
command returns 200 with the item as it is after the change, rendered for that actor with
`allowed_actions` empty, because they just performed the action and learn nothing new. A later `GET`
is a normal 404. The stored idempotent body is the same. Decided with Kumar.

## 39. Phase 4: smaller readings of the SPEC

- `assign` works from `new`, `in_progress` and `blocked`. SPEC 4.1 says "any open", but the note under
  the table forbids it while `awaiting_approval`; the note is explicit, so it wins.
- `unassign` is `POST /assign` with a null assignee (SPEC 11 has no unassign route). It needs `If-Match`
  like `assign`; `release` (own) does not.
- `resolve` takes `resolution` (default `done`); `reason` is the resolution note and the event reason.
- `request_approval` is allowed whether or not `requires_approval` is on (SPEC 4.1 only asks for "no
  pending approval"). `resolve` is what `requires_approval` gates.
- `workflow.evaluate` judges state and payload only. Who may act stays in `policy.py` (I3, I4;
  HANDOFF "policy.can() stays about people"). `allowed_actions` is `policy ∩ workflow`, and T-FLOW
  tests the intersection.
- Cancel and invalidate leave `approvals.decided_by` NULL: the four-eyes CHECK would reject a
  requester cancelling their own request.
- Claiming an item that is already mine answers 200 while it is open; on a resolved or closed item it
  is `WORKFLOW_VIOLATION`.
- **The claim UPDATE's guard is the only judge of claim state** (SPEC 6.1). Claim locks the row first
  (I5) and checks policy, but does not pre-check `status`/`assignee` in Python; otherwise the check
  after the lock would hide a missing `assignee_id IS NULL` and the sabotage row could not go red. The
  same rule is data in `workflow.py` (for `allowed_actions` and T-FLOW) and a test ties the SQL guard
  to it.

## 40. Phase 4: `approve` does not re-hash the content

A second check in `decide` (the pending approval's `subject_hash` must equal the hash of the item now)
would be harmless to correctness, but it would also hide the one guard the SPEC names. SPEC 4.4 makes
the invalidation inside the editing transaction, plus `If-Match` on approve, the protection. With a
re-hash in `approve`, removing the invalidation would make approve refuse instead of approving stale
content, and T-APPROVE-RACE would stay green. The test holds the invariant "an approved row always
covers the current content" over 50 rounds, so the invalidation is what it exercises.

## 41. Phase 4: two clauses of the conditional UPDATEs are redundant for the race

- The claim UPDATE has `assignee_id IS NULL AND status = 'new'` (SPEC 6.1). Either clause alone stops
  twenty claimants, because the winner changes both in the same statement. `assignee_id IS NULL`
  guards bad data (an owner on a `new` item) and has its own tests.
- `PATCH` compares the version in Python under the row lock (`ensure_version`) and again in the UPDATE
  (`version = :expected`). Either alone gives 412. Both are kept on purpose; each has its own test.

BUILD_PLAN's sabotage table says "remove `assignee_id IS NULL`: T-CLAIM goes red". It does not, for the
reason above; TESTING.md records what actually goes red for each clause.

## 42. Phase 4: whoever becomes an owner is checked against the membership row, locked

`claim`, `assign`, a `reopen` that gives the item back to its previous owner, and a `transfer` that
keeps the owner read the new owner's role with `SELECT ... FOR SHARE` on their `memberships` row
(`teams.share_lock_role`). Removing or demoting that person takes the row `FOR UPDATE` first
(`get_role(for_update=True)`), so one of the two waits: either the removal then finds and unassigns the
item (SPEC 4.3b), or the claim sees that the role is gone. The actor context is loaded before the
command (decision 34), so without this a claim could land just after a removal. The lock orders differ
(a command locks item then membership; a removal locks membership then items), so a deadlock is
possible; `run_command` retries it (40P01, up to 3 times). Tested by two race tests (15 and 8 rounds).

## 43. Phase 4: reading choices in `allowed_actions`

- **Resolve is offered without an approval.** The UI shows Resolve and the server answers 422
  `APPROVAL_REQUIRED`, which SPEC 12 says "points to Request approval" (and CB4's demo is "try to resolve
  a payment item"). `next_step` and the approval controls tell the person what to do first.
- **A second cancel** of an already cancelled request is 403 for a member (nobody "asked" any more) and
  409 for a lead. Policy decides who may cancel from the pending request, and there is none.
- **`GET` of an item with no pending approval** never offers `approve`, `reject` or `cancel_approval`.

## 44. Phase 4: unassigning a person's items is bounded, and deactivation has no endpoint yet

`unassign_owned_items` locks and clears the person's open items in the team 100 at a time inside the
membership transaction (I8). SPEC 4.3b also lists deactivating an account; there is no deactivate
endpoint in SPEC 11, so nothing calls it for that case. When one exists it must call this function for
each of the person's teams in the same transaction as the deactivation.

## 45. Phase 4 review: what an independent reviewer found, and what was decided

A reviewer that had not seen the session read `git diff phase-4-start..HEAD` and probed the code
against real Postgres. I reproduced each finding before changing anything.

Fixed (commit "fix(api): findings of the independent review of phase 4"):

- **High: a member could not release an item she could no longer see afterwards.** `release` answered
  through the visibility filter; once the item was unassigned (and confidential, which every
  compliance request is) she could not see it, so the answer was a 404 and the release was rolled back.
  Every command now renders its answer for the person who ran it without the filter (they were
  authorised first); `allowed_actions` comes out empty when they can no longer see it. This
  generalises decision 38 from transfer to all commands.
- **Medium: a command that waited for the row lock could be told the item did not exist.** The lock
  statement joined `teams`; after a concurrent transfer committed, Postgres re-checked the join against
  the old team and returned no row. `lock_by_key` now locks `work_items` alone and reads the team and
  the pending approval in a second statement (which also removes finding 4 below).
- **Medium: `duplicate_of` showed the key of an original the viewer cannot see.** The join in the item
  query now applies the visibility rule to the original (`visibility_clause(ctx, columns)`).
- **Low: the pending approval's requester was read in the locking statement,** so after a wait it came
  from the old snapshot and policy could see NULL. Now read after the lock (same change as above).
- Test gaps closed: hidden items for all eight commands, a lock held by another transaction (404 at
  once, not a wait), a transfer that commits while a command waits, confidential items end to end, and
  replays of claim, a lost claim, decision, request, cancel and transfer under one key.

Not changed:

- **`unassign_owned_items` has no overall bound** (about eight statements per item, one transaction).
  Known and written up in KNOWN_LIMITATIONS; the batch of 100 bounds memory, not time.
- **Share-lock sabotage is only probabilistic.** Replacing `share_lock_role` with a plain read made
  `test_claims_by_a_member_being_removed...` fail 3 of 3 runs and
  `test_assigning_someone_while_they_are_being_removed...` 1 of 3 (reviewer's measurement). Both pass
  with the lock. Accepted: a race test cannot be made certain, and the claim one is.
- **T-FLOW drives `allowed_actions`**, not `evaluate` per cell, and uses non-confidential facts. The
  `evaluate` rules (reasons, resolutions, approval gate) have their own tests; confidentiality is
  covered by the new edge tests.

## 46. Phase 5: search, attention and collaboration readings of the SPEC

Decided with the phase 5 prompt; none of them changes a SPEC rule, each fills a gap.

- **`q` is ranked, not paged.** `q` with `cursor` or an explicit `sort` is 400 (a silently ignored
  parameter is what decision 29 forbids); `limit` above 50 is cut to 50 (SPEC 8: "top 50"). A key-shaped
  `q` lists the exact key first, then the text matches. Rank = `ts_rank` (title and key weigh A,
  description B) + trigram `word_similarity` on the title; plain `similarity` would miss a typo in a
  long title. `/items/facets?q=` counts every match, not only the top 50.
- **`GET /items/similar` takes `team_id` as SPEC 8 and 11 say.** `TeamOut` had no id, so `GET /teams` now
  returns `id` (additive) or a client could not call it. `%` finds candidates through the GIN index, the
  explicit `similarity > 0.35` is the SPEC's threshold.
- **A comment answers `{comment, item}`.** The item so the client cache can merge by `last_event_id`
  (a comment never moves `version`, decision 8). `If-Match` is not needed; the key is required.
- **The item DTO gains `watching`.** SPEC 11's sample has none, but a Watch button cannot show its state
  without it. Watch/unwatch/read write no event and take no key (SPEC 11 lists none); both are
  naturally idempotent. Read is one `INSERT ... SELECT ... ON CONFLICT` that applies the visibility
  rule and takes `GREATEST` of the old marker, so it can neither exist for a hidden item nor go back.
- **Attention.** "Active" in "going quiet" is `in_progress` or `blocked` (the SPEC 3.2 index). "Needs an
  owner" is for members and leads only (a viewer cannot claim). "Your requests" means: the item's newest
  event is newer than your read marker (0 if you never opened it) **and was written by someone else**,
  so your own action is never news. Counts are `LIMIT 100`, shown as "100+" at 100; a section with fewer
  than 10 items skips the count query.
- **Notifications** list newest first by id; `unread=true` uses the partial index. SPEC lists no index for
  the full list, so it reads one user's rows (KNOWN_LIMITATIONS). Marking read touches only the caller's
  rows, so another person's id in the request is ignored. The worker that creates them is phase 6.

## 47. Phase 6 (lean): how the worker reads SPEC 9

- **`item.created` is enqueued by `create_item`,** in the command's transaction, with dedupe key
  `dup:<item_id>` (`db/tx.py: enqueue`). SPEC 9 lists the job but nothing wrote it; `record_event` only
  writes `item.event`. The outbox is still written only from `db/tx.py` and `repo/outbox.py` (the
  single-writer test lists both).
- **Claim and finish.** The SPEC claim query, with `now` passed in (never `now()` in SQL, so tests move the
  clock). Complete and fail are guarded by `status = 'pending'`: a runner that lost its lease and finishes
  late cannot undo a job another runner completed. A claim counts as a try, so a job whose runner died
  already has the lost try in `attempts`; the 8th try that fails (or is lost) makes it `dead`.
- **A hung handler is cut off at the lease** (`asyncio.timeout(lease)`) and counts as a failed try, so one
  poison job cannot block a runner. Cancellation (a killed process) is not a failure: the lease stays and
  another runner takes the job over after it expires. SIGTERM finishes the job in hand and releases the
  unstarted rest of the batch (their try is given back).
- **Notify checks visibility per recipient with `can_view`** (the policy's own function), judged against the
  item as it is when the job runs. Candidates are requester, assignee and watchers, minus the actor and
  deactivated users. It writes `NOTIFY notifications {user_id}` for each new row (phase 7 listens).
- **`duplicate_suggested` carries `{count, top_score}`, not the other items' keys:** the timeline is shown to
  everyone who can see this item, and they may not be allowed to see the others. The ids are in
  `item_similar`, which is filtered by whoever reads it. Written once per item (checked, since the dedupe
  key only guards the enqueue).
- **The SLA sweep** stamps `sla_breached_at` in `repo/items.py` (I1) 100 items per transaction under
  `pg_try_advisory_xact_lock`, and writes each `sla_breached` event through `record_event`, so the normal
  notify path fans it out. Each worker runs the sweep every 60 s; the lock and the `IS NULL` guard make
  that safe. The cleanup job and `FAULT_NOTIFY_FAIL_RATE` are skipped (phase 6 lean).
- **Admin.** `GET /admin/jobs?status=&cursor=&limit=` (keyset by id) and `POST /admin/jobs/{id}/retry`
  (dead only; 409 otherwise, 404 if missing), admins only.

## 48. Frontend session A (phases 8 and 9, lean): how the client reads SPEC 6.5-6.6 and DESIGN

- **Search is `GET /items?q=`,** not `/search`: the brief's name does not exist (SPEC 8 and 11). A ranked
  search takes no `sort` and no cursor, so `toListQuery` leaves `sort` out when `q` is set and the same
  infinite-query hook serves lists and search.
- **"Open" is four statuses, not an API value.** DESIGN 4.3 presets say `status=open`; the API's
  `status[]` has no such member. The URL carries `new, in_progress, blocked, awaiting_approval`, and the
  filter token reads "Status: Open" when exactly that set is selected (`isOpenSet`).
- **The idempotency key belongs to the user's attempt, not to a call.** `useCommand.execute` creates a key
  and puts it in the mutation variables, so TanStack's own retries (network, 503 BUSY, three times) reuse
  it. The New request form owns one key and replaces it only when a field changes or the create succeeds,
  so a double submit and a retry after a lost response are one request. A client that minted a key per
  `mutationFn` call would turn every retry into a second item.
- **`If-Match` is read when the mutation runs,** from the item cache, not when it is queued. With
  `scope: item:<id>` the second command on an item starts only after the first one's response has been
  merged, so it carries the new version (unit test: `"7"` then `"8"`).
- **One truth per item:** `mergeItem` keeps the higher `(version, last_event_id)`; equal replaces, so
  derived fields (`next_step`) refresh. `upsertItem` writes the detail cache, every list (all under
  `["items","list"]`) and the inbox; `removeItem` runs on 404. Errors that carry `current` (409, 412)
  are merged too, then lists refresh.
- **Layout answers to its own width.** The strip is a CSS grid in a container query: two lines (next step
  under the title) in the 480 px queue column, one line when wide (inbox). The one-line layout of DESIGN 4.1
  made titles unreadable next to the 560 px detail pane; DESIGN 3.1's own diagram shows two lines there.
- **Tokens only:** avatar hues (`--av-0..7`, each at least 5.5 : 1 against white) and `--on-hue` live in
  `tokens.css`; `--color-*: initial` removes Tailwind's default palette so a stray `stone-900` cannot compile.
  No raw colour outside `tokens.css` (checked by grep; the CI check is skipped).
- **Chosen small things:** the team picker is a native `<select>` (type-ahead works; a searchable combobox
  is not worth 8 teams); "Watch instead" is a plain `PUT /watch`; "Assign to me" is the only strip quick
  action (it exercises `useCommand` against the real claim); the right-hand pane shows what the list
  already knows (key, title, status, next step, people) until phase 10 builds the detail screen.
- **Tooling:** `openapi-typescript` declares TypeScript 5 as a peer; `package.json` `overrides` points it at
  the repo's TypeScript 6 (it only uses the compiler API). `npm run gen:api` is a Node script because npm
  runs scripts through cmd.exe on Windows (`${VAR:-x}` fails); `OPENAPI_URL` overrides the default
  `http://localhost:8080/api/openapi.json`.

## 49. Frontend session B (phase 10, lean): item detail, conflicts, polling

- **Two dependencies added, both inside the locked stack.** `@radix-ui/react-popover` (DESIGN 4.4 asks
  for "a small popover with a required text field"; the stack line is "Radix UI") and `diff` (jsdiff, named
  in the stack's Content row, for the word-level conflict diff).
- **Live updates are 10 s polling, not SSE.** Phase 7 is not built; BUILD_PLAN allows it. `useLiveUpdates`
  keeps the SSE interface (`{ itemKey }` in, caches kept fresh, nothing out): it refetches the open item and
  its timeline plus the *rendered* lists, and stops while the tab is hidden. No 250 ms coalescing: one
  tick is one refetch. Costs: up to 10 s of lag (measured 9 s), and a poll per open tab.
- **The item query merges.** `itemQuery`'s function returns `mergeItem(cached, fetched)`, so a slow poll that
  lands after a mutation response cannot roll the screen back (I13). Lists still replace wholesale on
  refetch; a list page older than a just-applied mutation can briefly show old data until the next poll.
- **A field edit sends the version the editor *started from*, not the cache's.** The description draft
  stores `baseVersion`; `If-Match` uses it. Reading the cache instead would make the edit succeed after a
  poll brought in someone else's change, which is a silent lost update. A 412 goes through
  `runWithRebase`: no overlap between my fields and `changes_since` fields (read from each event's
  `data` keys) means resend on `error.current.version` with a **new** idempotency key (decision 34) and
  say "Saved. Asha's change to priority was kept."; overlap, or a 412 without `changes_since`, opens the
  ConflictDialog. "Save my version" resends on the server's version; Esc keeps the draft.
- **The server decides when a reason is needed.** Lowering priority is optimistic; on 422
  `REASON_REQUIRED` the optimistic value is rolled back and a reason popover opens. The UI does not know
  "P0/P1". Likewise `APPROVAL_REQUIRED` on resolve shows "Request approval" instead of the UI guessing.
- **Which actions appear is `allowed_actions`; how they look is `features/item/actions.ts`.** Labels, the
  form each one collects, and the primary (first preference for `next_step.kind` that the server allows).
  A test runs every pair of actions through `barModel` and checks nothing outside `allowed_actions`
  is ever offered. The close form hides "resolution" when the item is `resolved` (form shape, not a rule).
- **Stale approve.** `approve` uses the cache version at click time, so an approver whose screen already
  shows the change (a poll arrived) approves what they can see; one who clicks before the poll gets 412
  and "changed since you opened it" with the events and a word diff, then "Approve this version" (the
  retry carries the version from `error.current`). A material edit also withdraws the approval, so the
  button disappears after the 412 and the panel says nothing is left to approve.
- **Names for ids.** `assigned`/`unassigned`/`transferred` events carry user and team **ids**. The track
  and timeline resolve them from actors, the item's people, the team's first 50 members and `/teams`.
  Someone who left the team and never acted on the item reads "someone" (KNOWN_LIMITATIONS).
- **Timeline order and bounds.** Events are fetched newest first (`order=desc`, 100 a page), at most five
  pages (500 events); the track needs the whole ownership history. The unread divider uses
  `unread_since_event_id` as it was when the item opened (the open itself clears it server-side), so
  `Loaded` is keyed by item key and reads it once.
- **Handoff track motion.** A segment grows in (220 ms) only when it appears after the events first loaded;
  state is adjusted during render, not in an effect. `prefers-reduced-motion` zeroes `--dur-grow`/`--dur-wash`.

## 50. Phase 14 (lean): SPEC text brought in line with the code

At submission the SPEC described three things the code does not do. The code stays; the SPEC says so, in
place, with "not built in v1" markers, so a reader is never told a behaviour that is not there. Each is
also in KNOWN_LIMITATIONS.

- **SPEC 10 and 11, live updates.** `GET /stream` (SSE) is not built; the client polls every 10 s
  (decision 49). A note at the top of section 10 says so and keeps the rest as the design of the SSE
  replacement; the `/stream` row of the API table is marked. `NOTIFY` is sent but has no listener.
  `docs/ARCHITECTURE.md` draws what exists.
- **SPEC 7 and 11, `GET /stats/teams` and `GET /decisions`.** Not built (moved out of phase 5 in
  HANDOFF, then not reached). Marked in section 7 and in the API table. There is no dashboard or
  decisions screen.
- **SPEC 9, cleanup job and fault injection.** The hourly cleanup job is not built, and
  `FAULT_NOTIFY_FAIL_RATE` is read by `Settings` and refused in prod (decision 2) but the notify handler
  does not use it. Marked in the job table and the paragraph.
- **SPEC 13, performance targets.** There is no `docs/PERFORMANCE.md` or `scripts/bench.py`; section 13 now
  says the targets were not measured.
- **Already consistent, checked:** search is `GET /items?q=` and `GET /items/facets?q=` (SPEC 8, decision
  46); `GET /items/similar` and `GET /teams` returning `id` (decision 46); no `POST /teams` (decision 20);
  the listed error codes (`test_errors.py`).
- **Not a SPEC fix:** CLAUDE.md still lists SSE in the stack table and `--profile e2e` in Commands; both
  describe the plan, not the submission. README and TESTING say what runs today.
