# Baton

Internal web app for coordinating operational work across teams: requests that need
investigation, action or approval, handed between people until they're resolved.

Work in progress. Phase 0 (foundation) only: the stack starts and tests run.

## Run it

```bash
docker compose up --build
```

Open http://localhost:8080.

If port 8080 is taken, set WEB_PORT=8081 in .env.

## Test it

```bash
docker compose -f compose.yaml -f compose.test.yaml run --rm api-test
docker compose -f compose.yaml -f compose.test.yaml run --rm web-test
```

The API tests need PostgreSQL and fail, never skip, when `TEST_DATABASE_URL` is missing.

More (assumptions, architecture, known limitations) arrives with the later phases; see `docs/`.
