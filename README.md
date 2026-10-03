# Baton

Baton is an internal web app for coordinating operational work across teams: requests that need
investigation, action or approval, handed between people until they are resolved.
It is built to stay correct when people act at the same time: atomic claims, optimistic versions,
idempotent writes, content-bound approvals, resource-level authorization and an outbox worker.
Stack: FastAPI + PostgreSQL 16 (state, queue, search) + React/TypeScript, all in Docker Compose.

## Quick start

Needs Docker with Compose v2. Nothing else is installed on the host.

```bash
cp .env.example .env          # optional: every value also has a default in compose.yaml
docker compose up --build
```

Open **http://localhost:8080** and sign in with a demo account (password for all: `baton-demo`).
The first start builds the images and loads the demo data; wait until the `web` container is up.

| Sign in as | Who | Good for |
|---|---|---|
| `priya.lead@baton.test` | Payments lead | triage, approvals, assigning |
| `asha@baton.test`, `rahul@baton.test` | Payments members | claiming and working items (two people for the race) |
| `meera@baton.test` | Support member, Payments viewer | raising requests, viewer limits |
| `dev.viewer@baton.test` | viewer on Payments and SRE | read-only: actions refused |
| `farah@baton.test` | Compliance member | cannot see others' confidential items (404) |
| `ishaan.lead@baton.test` | Compliance lead | sees confidential items |
| `sunita.lead@baton.test` | Support lead | |
| `admin@baton.test` | admin | sees everything, Jobs page; cannot approve unless also a lead |

The login page also has a quick account switcher while `DEMO_MODE=true`.

**Reset the demo data:** `docker compose run --rm seed python -m scripts.seed --size demo --reset`

**Port 8080 taken?** Set `WEB_PORT=8081` in `.env` (or `WEB_PORT=8081 docker compose up --build`) and open
http://localhost:8081. The `web` container is the only published port.

A guided tour of the critical behaviours is in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md).

## Run the tests

```bash
docker compose -f compose.yaml -f compose.test.yaml run --rm api-test   # ~6 min, real Postgres, none skipped
docker compose -f compose.yaml -f compose.test.yaml run --rm web-test   # ~10 s, Vitest
```

Both use a separate `db-test` container. Add `--build` after dependency or Dockerfile changes. Playwright
end-to-end tests run from the host against a running stack; see [docs/TESTING.md](docs/TESTING.md).

## Screens

| Screen | Path | What it answers |
|---|---|---|
| Sign in | `/login` | demo accounts in demo mode |
| Inbox | `/inbox` | what needs me: approvals, urgent, unowned, going quiet, my requests |
| Queue and item | `/items`, `/items/PAY-142` | filter, search, act on one request, its handoff track and timeline |
| Dashboard | `/dashboard` | per team: open by status and priority, overdue, unowned, going quiet, oldest items, load per owner; every figure links to the matching queue |
| Decisions | `/decisions` | why things were approved, closed, moved or downgraded, with the reason; filter by team and kind |
| Notifications | the bell in the rail | unread count (capped at "20+"), latest updates grouped by item, mark read |

Not built: team settings, the jobs page, the command palette (see `docs/KNOWN_LIMITATIONS.md`).

## Repo map

```
compose.yaml  compose.test.yaml  .env.example
api/                    FastAPI service, worker, migrations, seed (Python 3.12)
  app/domain/             pure rules: workflow.py, policy.py (no I/O)
  app/services/           one transaction per command
  app/repo/               SQL (SQLAlchemy Core); the only writers of work_items/events/outbox
  app/api/                routers, schemas, idempotency, CSRF, problem+json errors
  app/worker/             outbox runner and SLA sweep (python -m app.worker)
  alembic/                migrations
  scripts/                seed.py, verify_seed.py, bench.py
  tests/                  unit/ db/ integration/ concurrency/ perf/
web/                    React + TypeScript + Vite SPA (served by nginx in Compose)
  src/features/           inbox, queue, item, create, dashboard, decisions, notifications, auth
  e2e/                    Playwright
docs/                   see below
```

## Assumptions

Copied from the spec (`docs/SPEC.md`, section 1), where each is explained.

- **A1.** Users are employees. Production would use company SSO; v1 uses email + password with seeded accounts. _(Auth is real (sessions, hashing, CSRF) but not the focus.)_
- **A2.** Anyone can raise a request to any team. The receiving team triages it. _(Matches "teams receive requests" — the requester is often outside the team.)_
- **A3.** An item belongs to exactly one team at a time and can be transferred. _(Clear accountability; routing mistakes are fixable.)_
- **A4.** An item has **one owner** at a time. Others collaborate as watchers and commenters. _(A single owner is what stops two people silently working the same issue.)_
- **A5.** Approvals are given by a lead of the owning team, and never by the person who asked for the approval. _(Four-eyes rule.)_
- **A6.** An approval covers the content that was approved. Changing title, description, type or team afterwards needs a fresh approval. _(Prevents "approved one thing, executed another".)_
- **A7.** Due dates default from priority, in calendar hours (no business hours or holidays). _(Simple, explainable SLA.)_
- **A8.** History is never edited or deleted. Corrections are new events. _(The audit trail must be trustworthy.)_
- **A9.** Times are stored in UTC and shown in the browser's local time. UI is English. _(—)_
- **A10.** Desktop first (people at desks working queues); usable on tablet and phone for reading and quick actions. _(Shapes layout density.)_
- **A11.** Notifications are in-app only in v1. Email/Slack would be another consumer of the same outbox. _(Async design already supports more channels.)_
- **A12.** Compliance requests are **confidential** by default: inside the team, only leads, the owner and the requester can see them. _(Gives authorization a real resource-level rule.)_
- **A13.** Any signed-in user can list a team's members and search the user directory. _(Needed for routing requests and for assignee and member pickers.)_
- **A14.** Demo accounts and the shared demo password exist only for the demo seed, and are offered only when `DEMO_MODE=true`. _(Real deployments have no well-known credentials; the API refuses demo mode in production.)_

## Documents

| Doc | What it holds |
|---|---|
| [docs/ENGINEERING_DECISIONS.md](docs/ENGINEERING_DECISIONS.md) | top 5 decisions with trade-offs, then the full numbered log |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | components, flows, scaling path |
| [docs/TESTING.md](docs/TESTING.md) | what is tested, how to run it, risk → test map (CB1–CB8), what is not tested |
| [docs/KNOWN_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) | what is missing or partial, ordered by importance |
| [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md) | 5-minute demo |
| [docs/SPEC.md](docs/SPEC.md) | what the system does: data model, workflow, permissions, API, errors |
| [docs/DESIGN.md](docs/DESIGN.md) | look, layout and copy |
| [docs/REQUIREMENTS_TRACE.md](docs/REQUIREMENTS_TRACE.md) | every brief requirement → where it is built and proven |
| [docs/HANDOFF.md](docs/HANDOFF.md), [docs/NOTES_FOR_INTERVIEW.md](docs/NOTES_FOR_INTERVIEW.md), [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) | working notes and plan |
