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

_SPEC section 14 guards are added as they are built._

## Sabotage checks

_What was removed, which test failed._
