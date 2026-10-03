# Baton

Internal web app for coordinating operational work across teams: requests that need
investigation, action or approval, handed between people until they're resolved.

Work in progress. Phases 0 to 2 are done: the stack, the database schema and demo data, and sign-in
with the authorization model (nothing to click yet except the health page; the work-item endpoints
start in phase 3).

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

More (architecture, known limitations) arrives with the later phases; see `docs/`.
