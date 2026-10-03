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

## 8. Phase 1: every command that writes an event bumps the item version

Comments and system events (SLA breach, duplicate suggestion) also increment `version`, because
they write `work_items` (`last_event_id`, `last_activity_at`) and I2 says every such write does.
That also makes "updated since you looked" and live updates work for comments. All events of one
command share one `item_version`. To be confirmed when phase 3 writes `record_event()`.

## 9. Phase 1: event `team_id` is the team at the moment each event is written

For a transfer, the `transferred` event carries the new team; an `approval_cancelled` written in
the same command before the team moves carries the old team. SPEC 3.2 says only "item's team at
the time of the event". To be confirmed in phase 4.

## 10. Phase 1: indexes are exactly the SPEC list; sort-expression question deferred

SPEC 3.2 indexes `due_at` plainly while SPEC 8 sorts and pages on `COALESCE(due_at, 'infinity')`.
A keyset cursor on that expression cannot use the plain index fully. Built as listed; if
`EXPLAIN` in phase 3 or 12 shows a sort, a new migration swaps in expression indexes under the same names.

## 11. Phase 1: TRUNCATE is not covered by the append-only trigger

SPEC 3.2 specifies a trigger on UPDATE and DELETE. `seed --reset` needs TRUNCATE, so it is left
open. Application roles in a real deployment would not be granted TRUNCATE (see KNOWN_LIMITATIONS later).
