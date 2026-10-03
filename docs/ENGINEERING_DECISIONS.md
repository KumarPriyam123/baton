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
