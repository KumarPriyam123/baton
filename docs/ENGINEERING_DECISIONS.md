# Engineering decisions

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
