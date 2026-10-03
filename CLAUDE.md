# CLAUDE.md — Baton

Baton is an internal web app for coordinating operational work across teams: requests that need investigation, action or approval, handed between people until they're resolved. It's Kumar's submission for the Newtonite "Operations Under Pressure" engineering challenge. Reviewers will run it, read it, and ask him to explain or change any part live, so **correct, explainable behaviour beats feature count**.

## Source of truth

| Order | File | Holds |
|---|---|---|
| 1 | `docs/SPEC.md` | what the system does: data model, workflow, permissions, API, errors |
| 2 | `docs/DESIGN.md` | look, layout, copy, interaction rules |
| 3 | `docs/BUILD_PLAN.md` | phase order, prompts, definitions of done |
| 4 | this file | rules for every session |

If code and SPEC disagree, the code is wrong. If SPEC looks wrong, **stop and ask**; don't silently pick another interpretation. Agreed changes to SPEC are recorded in `docs/ENGINEERING_DECISIONS.md`.

## Stack (locked)

| Layer | Choice |
|---|---|
| API | Python 3.12, FastAPI, Uvicorn, Pydantic v2, pydantic-settings |
| Data access | SQLAlchemy 2.0 **Core** (async) + asyncpg; explicit SQL for critical paths; Alembic migrations (hand-written SQL allowed) |
| Database | PostgreSQL 16 with `citext`, `pg_trgm` |
| Worker | same codebase, separate process (`python -m app.worker`) |
| Live updates | Server-Sent Events fed by Postgres `LISTEN/NOTIFY` |
| Auth | argon2-cffi, server-side sessions in Postgres, CSRF double-submit |
| Logging | structlog JSON with request id |
| API tests | pytest, pytest-asyncio, httpx, hypothesis, time-machine |
| Python tooling | uv, ruff, mypy (strict for `app/domain`, `app/services`) |
| Web | React + TypeScript (strict) + Vite, React Router |
| Server state | TanStack Query v5 (+ TanStack Virtual for long lists) |
| UI primitives | Radix UI, cmdk, Sonner, lucide-react, Tailwind CSS v4, clsx + tailwind-merge |
| Forms | react-hook-form + zod |
| API client | openapi-typescript + openapi-fetch, generated from the API's OpenAPI |
| Content | react-markdown + remark-gfm (**no** rehype-raw), jsdiff, date-fns |
| Web tests | Vitest, Testing Library, MSW, Playwright, @axe-core/playwright |
| Run | Docker Compose: `db`, `migrate`, `seed`, `api`, `worker`, `web` (nginx) |
| CI | GitHub Actions |

**Not without a recorded decision:** Redis, Kafka, Celery, Elasticsearch, Next.js, an ORM-generated schema, another client state library, any new runtime dependency. Postgres already provides the queue, search and pub/sub; that's a deliberate decision (ENGINEERING_DECISIONS #1).

## Repository layout

```
CLAUDE.md  README.md  compose.yaml  compose.test.yaml  .env.example  .gitattributes
api/
  pyproject.toml  alembic/  
  app/
    main.py  config.py
    db/        schema.py  tx.py (command_tx, record_event)  errors.py (constraint → domain error)
    domain/    enums.py  policy.py  workflow.py  hashing.py      # pure, no I/O
    repo/      items.py  events.py  attention.py  search.py  stats.py  approvals.py
    services/  commands.py  items.py  comments.py  teams.py       # one transaction per command
    api/       routers/  schemas/  deps.py (actor context)  idempotency.py  errors.py  csrf.py
    auth/      passwords.py  sessions.py
    realtime/  listener.py  hub.py  stream.py
    worker/    __main__.py  runner.py  handlers/  schedules.py
  scripts/     seed.py  bench.py                                 # run inside the api image
  tests/       unit/  db/  integration/  concurrency/  perf/
web/
  src/
    app/         router.tsx  providers.tsx  shell/
    api/         generated.ts  client.ts  keys.ts
    lib/         api.ts  itemCache.ts  useCommand.ts  rebase.ts  useLiveUpdates.ts  shortcuts.ts
    features/    inbox/  queue/  item/  create/  dashboard/  decisions/  teams/  jobs/  auth/
    components/ui/
    styles/      tokens.css  globals.css
  e2e/
docs/      SPEC.md  DESIGN.md  BUILD_PLAN.md  REQUIREMENTS_TRACE.md  HANDOFF.md  NOTES_FOR_INTERVIEW.md
           TESTING.md  PERFORMANCE.md  ENGINEERING_DECISIONS.md  ARCHITECTURE.md  KNOWN_LIMITATIONS.md  DEMO_SCRIPT.md
```

## Commands

Everything runs in containers so it works the same on Windows, macOS and Linux.

| Task | Command |
|---|---|
| Start everything | `docker compose up --build` → http://localhost:8080 |
| API tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` |
| Web unit tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test` |
| End-to-end | `docker compose -f compose.yaml -f compose.test.yaml --profile e2e up --abort-on-container-exit` |
| Large seed | `docker compose run --rm seed python -m scripts.seed --size large --reset` |
| New migration | `docker compose run --rm migrate alembic revision -m "<msg>"` |
| Regenerate API types | `cd web && npm run gen:api` (API must be running) |
| Two workers | `docker compose up --scale worker=2` |
| Test containers | `api-test` mounts `./api` and `web-test` mounts `./web/src`, so source changes need no rebuild. After dependency or Dockerfile changes, add `--build` (e.g. `run --rm --build api-test`). |

## Invariants — never break these

| # | Rule |
|---|---|
| I1 | Every change to a work item goes through `record_event()`: exactly one `item_events` row per change, in the **same transaction**, with `item_version` = the item's version after the change, plus the outbox row and `NOTIFY`. No other code writes to `work_items`. |
| I2 | `version` increments by 1 per command that changes a decision-relevant field (title, description, type, priority, status, assignee, team, due_at, confidential, requires_approval, approval state, resolution). Comments, watch changes, `duplicate_suggested` and `sla_breached` do not bump it: they write an event (except watch) with `item_version` = the current version and update `last_event_id`. Commands whose meaning depends on what the user saw require `If-Match` (SPEC §6.2). |
| I3 | Permissions are decided on the server in `domain/policy.py`. Every query that returns items **or counts** uses `visibility_clause()`. Not visible → 404; visible but not allowed → 403 with a reason. |
| I4 | Workflow rules live only in `domain/workflow.py`. The UI renders actions from `allowed_actions` and never decides transitions or permissions itself. |
| I5 | A command is one short transaction via `command_tx()`: lock the item row first, then approvals; no network calls inside; lock and statement timeouts set. |
| I6 | Every state-changing POST goes through the idempotency layer. The client creates one key per user action and reuses it on every retry. |
| I7 | Async work is enqueued only through the outbox, in the same transaction as the change. Every handler is idempotent. Scheduled jobs take a Postgres advisory lock. |
| I8 | No unbounded reads in request paths: keyset pagination with a max limit, no `OFFSET`, never load a whole table into memory or the browser. |
| I9 | History is append-only: `item_events` has a trigger rejecting UPDATE/DELETE; comments have no edit/delete routes. |
| I10 | Every critical rule in SPEC §3.2 also exists as a database constraint. Application checks give good errors; constraints catch bugs. |
| I11 | Tests that need Postgres run against real Postgres and **never skip**. Concurrency tests use separate connections released together. |
| I12 | User content is never rendered as raw HTML. SQL is always parameterised. No secrets, `.env` or the brief PDF in git. |
| I13 | Frontend server state lives in TanStack Query. Items merge by `(version, last_event_id)` (never older over newer). Mutations on one item share a `scope`. |
| I14 | UI uses design tokens only (no raw hex outside `tokens.css`), follows DESIGN.md copy, and every async state (loading, empty, error, pending) is designed. |

## Conventions

- **Errors:** raise domain exceptions with a code from SPEC §12; the API layer turns them into problem+json. Never return ad-hoc error shapes.
- **Migrations:** never edit a migration that has been applied; add a new one. Name constraints explicitly.
- **Names:** API JSON is snake_case; TypeScript uses the generated types as-is. Event kinds and error codes come from SPEC, not invented per endpoint.
- **Time:** `timestamptz` in UTC everywhere; the browser formats local time.
- **Logs:** one JSON line per request (method, route, status, latency_ms, user_id, request_id); worker lines include the originating request_id.
- **Commits:** small and real, conventional messages (`feat(api): atomic claim`), one logical change each. No squashing a phase into one commit, no backdating.
- **Tests name the risk:** `test_claim_twenty_concurrent_requests_exactly_one_wins`, not `test_claim_2`.

## How to work

1. One phase per session (BUILD_PLAN.md). Restate its "Done when" list before coding.
2. Build in small steps; run the relevant tests after each.
3. **Never mark something done that you didn't verify.** If a tool (browser, Docker) wasn't available, say "unverified" and why.
4. Look at UI work: take Playwright screenshots and actually view them before calling a screen done.
5. Don't add a dependency, service or pattern outside this file without asking and recording the decision.
6. At the end of each phase: update `docs/REQUIREMENTS_TRACE.md`, `docs/HANDOFF.md`, `docs/NOTES_FOR_INTERVIEW.md` (problem → root cause → fix → lesson, plus the "explain this phase" summary).
7. Prefer the simplest design that satisfies SPEC. A smaller, well-reasoned system is the goal.

## Environment hazards (Kumar's machine)

- **Windows + Git Bash.** `.gitattributes` forces LF; a CRLF entrypoint fails inside Linux containers with `/bin/sh^M: not found`. Use `/c/Users/...` paths in Git Bash, not backslashes.
- **No `make`.** Use the `docker compose` commands above; don't add Makefile-only workflows.
- **Port 8080** must be free; only `web` publishes a port.
- **LISTEN/NOTIFY needs a direct Postgres connection**, never a transaction-mode pooler.
- **One scheduler.** Never run scheduled jobs inside API processes; only the worker runs them, under an advisory lock.
