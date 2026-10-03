# Handoff

Where the project stands, how to run it, and what to know before the next session.

## Current state

**Phase 0 (foundation) is done. Phase 1 has not started.** Tag `phase-0-done`; `phase-0-start` marks the beginning.

Repo: https://github.com/KumarPriyam123/baton (private). CI is green on `main` (api, web, hygiene).

What exists:

- `api/`: FastAPI app factory (`app.main:create_app`), `GET /api/v1/healthz`, pydantic-settings config with the prod guard, structlog JSON logs, request-id middleware (also turns crashes into problem+json). Empty packages for the layers later phases fill (`db`, `domain`, `repo`, `services`, `auth`, `realtime`). `app/worker` is a heartbeat loop. 20 tests.
- `web/`: Vite + React + TypeScript (strict) + Tailwind v4, ESLint 9, Prettier, Vitest. One placeholder page that calls healthz and shows loading, ok and error states. 4 tests.
- Docker: `db`, `migrate` (placeholder `echo`), `api`, `worker`, `web` (nginx; proxies `/api`; `/api/v1/stream` unbuffered with a 1 h timeout). `compose.test.yaml` has `db-test` (tmpfs), `api-test`, `web-test`.
- CI (`.github/workflows/ci.yml`): `api` (ruff, ruff format, mypy, pytest on a postgres service), `web` (eslint, prettier, tsc, vitest, build), `hygiene` (no CRLF, no tracked PDF or `.env`, gitleaks over full history).
- Not built yet, by design: `seed` service, Alembic setup, any table, auth, CSRF, CSP headers; `/readyz` is a phase 2 item.

Verified: everything in the Phase 0 Done-when list; see `docs/REQUIREMENTS_TRACE.md`.
**Deferred:** `docker compose up --build` on port 8080 itself (another local project holds it). Verified on 8081 and 8082. Re-check at the phase 14 clean-clone run.

## Commands

| Task | Command |
|---|---|
| Start everything | `docker compose up --build` (http://localhost:8080; if taken, `WEB_PORT=8081` in `.env`) |
| API tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm api-test` |
| Web unit tests | `docker compose -f compose.yaml -f compose.test.yaml run --rm web-test` |
| After dependency or Dockerfile changes | add `--build`: `run --rm --build api-test` |
| Tear down (also stops `db-test`) | `docker compose -f compose.yaml -f compose.test.yaml down --remove-orphans` |
| API lint and types on the host | `cd api && uv run ruff check . && uv run ruff format --check . && uv run mypy app tests` |
| Web checks on the host | `cd web && npm run lint && npm run format:check && npm run typecheck && npm test && npm run build` |
| Secret scan | `MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W):/repo" zricethezav/gitleaks:v8.30.1 detect --source /repo --no-banner` |

pre-commit is installed in this clone (`uv tool install pre-commit`, then `pre-commit install`): ruff, ruff-format on `api/`, gitleaks.

## Hazards

- **Port 8080** is held by `infra-latex-1` (an unrelated project). Do not stop it. Use `WEB_PORT`.
- **Stale images:** `docker compose run` never rebuilds. `api-test` and `web-test` mount the source to avoid it; dependency or Dockerfile changes still need `--build`.
- **`docker compose run` leaves `db-test` running** (it is a dependency). Use the tear-down command above.
- **Git Bash:** paths in `docker run -v` get mangled; prefix `MSYS_NO_PATHCONV=1` and use `pwd -W`. Large heredocs containing quotes can fail to parse; write files with the Write tool.
- **Route templates:** FastAPI 0.142 loses the `include_router(prefix=...)` part of the matched route. Routers must declare `APIRouter(prefix=API_PREFIX + "/...")` themselves, or the log `route` field loses `/api/v1`.
- **ESLint is pinned to 9** until `eslint-plugin-jsx-a11y` supports 10.
- **Git identity** in this repo is `kpriyam2005p@gmail.com`; the account email is `k2005priyam@gmail.com`. Unconfirmed which is intended.
- The first commit (`0f1150e` docs) has no `Co-Authored-By` trailer; later ones do.
- Never edit an applied migration (none exist yet).

## Next phase

Phase 1: schema, migrations, seed data (SPEC §3).

- Replace the `migrate` placeholder command with `alembic upgrade head`; create `api/alembic/env.py` (async) and the first hand-written migration.
- Add the `seed` compose service and `scripts/seed.py`.
- `/readyz` (DB reachable, migrations at head, SPEC §11) moves to **phase 2**: it needs the database engine and app lifespan, which arrive there.
- Tests under `api/tests/db` run on the existing `db-test`; each needs a clean schema per run.
- SPEC §4 reopen rules were corrected in phase 0 (`docs(spec): fix reopen target and table layout`); phase 4 should follow the corrected text.
