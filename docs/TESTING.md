# Testing

## How to run

```bash
docker compose -f compose.yaml -f compose.test.yaml run --rm api-test
docker compose -f compose.yaml -f compose.test.yaml run --rm web-test
```

Source is mounted into both containers; add `--build` after dependency or Dockerfile changes.
End-to-end tests arrive in phase 13. A skipped test counts as a failure.

## What each layer proves

| Layer | Where | Needs | Proves |
|---|---|---|---|
| Unit | `api/tests/unit` | nothing | pure rules |
| DB | `api/tests/db` | Postgres | constraints, SQL |
| Integration | `api/tests/integration` | Postgres | API behaviour |
| Concurrency | `api/tests/concurrency` | Postgres | races (separate connections) |
| Performance | `api/tests/perf` | large seed | latency targets |

## Tests that must go red when their guard is removed

| Guard | Test | Phase |
|---|---|---|
| No database tests skipped silently | `test_pytest_fails_instead_of_skipping_when_test_database_url_is_unset` | 0 |
| Prod refuses `DEMO_MODE` and `FAULT_*` | `tests/unit/test_config.py` | 0 |
| Request id is validated, echoed and logged | `tests/integration/test_request_context.py` | 0 |

| Every rule in SPEC 3.2 is enforced by the database, under its own name | `tests/db/test_constraints.py::test_database_rejects_it_and_names_the_guard` (28 cases) | 1 |
| ...and that test can pass only because of that guard | `test_constraints.py::test_it_only_fails_because_of_that_guard`: drops the guard inside the test transaction and the same statement must succeed | 1 |
| Every SPEC 3.2 index, primary key and table exists as specified, and no unlisted index exists | `tests/db/test_indexes.py` | 1 |
| Migration is reversible and repeatable | `tests/db/test_migrations.py` (catalog snapshot equal after up, down, up) | 1 |
| Python tables and enums cannot drift from the database | `tests/db/test_schema_drift.py` | 1 |
| Seeded histories are valid, and the verifier is not vacuous | `tests/unit/test_seed_histories.py` (6 seeds x 600 histories; 13 kinds of corruption must be reported) | 1 |

_SPEC section 14 guards are added as they are built._

### Database tests (`api/tests/db`)

- `conftest.py` rebuilds the `public` schema once per session and runs `alembic upgrade head`, so
  the tests exercise the real migration, not a copy of the schema.
- Each test gets a connection inside a transaction that is always rolled back, so tests leave no rows
  behind and can run in any order. A violating statement runs in a savepoint so the connection stays usable.
- `violations.py` is a registry: one entry per guard, with the SQL that breaks it and the SQL that
  removes it. Adding a constraint to the schema without an entry here is visible in review; adding
  an entry gives two tests (rejects, and fails only because of the guard).
- `test_seed.py` builds a scratch database, loads the demo seed, and checks the data (counts, every
  history, key and counter consistency, something to demonstrate per role). It also times the load
  (budget 30 s; measured about 1 s).
- Everything here needs real Postgres and never skips.

### Seed verification (`scripts/seedlib/verify.py`)

`python -m scripts.verify_seed [--sample N]` replays `item_events` for each item and checks, at
every command boundary, that the state obeys the database rules, that each status change is one
its event kind may cause, that versions are contiguous, that decisions carry reasons, and finally
that the replayed state equals the stored row and the approvals agree with their events.
The check is deliberately independent of the generator (`sim.py`).

Measured on the large seed (50,000 items, 586,983 events): every history valid, 17 s.

## Sabotage checks

Phase 1, by hand: the `owner_when_active` CHECK was deleted from `0001_initial_schema.py` and
`pytest tests/db` run. Five tests went red: `test_database_rejects_it_and_names_the_guard[active_item_without_owner]`,
`test_it_only_fails_because_of_that_guard[active_item_without_owner]` and the three
`test_every_active_status_requires_an_owner[...]` cases. The file was restored byte for byte
(`cmp` identical).

The same check is automated for every database guard (next section), so it is not only a one-off.


## Phase 2 guards (identity, sessions, authorization, visibility)

| Guard | Test | Sabotage observed |
|---|---|---|
| Every cell of SPEC 5.2 | `tests/unit/test_policy_matrix.py`: 11 scenarios x 25 actions x 9 personas (2,475 cells) plus team, organisation and combined-role rows. Expectations are typed by hand from the SPEC; a second test fails if the SPEC's action list and the policy's differ | `CLAIM` allowed for any role: 24 cells red (viewers claiming) |
| `visibility_clause` equals `can_view` | `tests/db/test_visibility_parity.py` (T-VIS): all 24 demo users and 300 random role combinations over 600 items; also counts, `LIMIT` after filtering, and a clause that forgets the confidential rule must be detected | clause without the confidential rule: the dedicated test goes red and shows it leaks exactly the confidential items |
| Invisible item is 404, never 403 | matrix (`NOT_FOUND` for every persona that cannot see) and parity (farah, a Compliance member, cannot see 5+ confidential items she neither owns nor raised) | |
| CSRF on every unsafe route | `tests/integration/test_csrf.py` reads the app's own OpenAPI, so later routes are covered automatically; a route added in the test is protected without touching the middleware | check returning `True`: 6 tests red |
| Only the token hash is stored; logout kills the token | `tests/integration/test_auth.py` | `token_hash` returning the token: 2 tests red |
| Idle expiry, deactivation, role changes apply at once | `test_auth.py` (time-machine for expiry; direct SQL for deactivation and membership removal) | |
| Throttling | `tests/integration/test_throttling.py`: 6th attempt 429 even with the right password; same answer for unknown email; lock expires at 15 min; counter restarts after a lock; parallel failures all counted | |
| Every SPEC 12 code | `tests/integration/test_errors.py`: expected table typed by hand; validation errors never echo input; crashes never leak internals | |
| Membership rules | `tests/integration/test_teams_members.py`: leads vs admins vs everyone else, removal effective on the next request, refusal while they own open items, keyset paging | |

The integration tests run the real app, with its lifespan, against a scratch database holding the
committed demo seed (`tests/support/seeded.py`), so sessions, cookies and SQL are the real ones.
Time-dependent behaviour uses `time-machine`; every timestamp that matters is passed in from
Python (never `now()` in SQL) so the tests can move the clock.

Whole suite at the end of phase 2: 2,853 API tests, about 70 s.
