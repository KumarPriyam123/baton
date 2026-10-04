# Notes for the interview

Written for Kumar, to explain any part of Baton live.

## Problems hit

Each entry: **problem → root cause → fix → lesson**.

### Phase 0

**1. Tests ran old code and still looked fine**
- Problem: after fixing a bug, `api-test` failed with the same assertion as before.
- Root cause: `docker compose run` reuses an existing image; it never rebuilds. The container had the old source baked in.
- Fix: `api-test` mounts `./api`, `web-test` mounts `./web/src`. Dependencies live in `/opt/venv` and `node_modules`, outside the mount, so the mount can't hide them. Dependency or Dockerfile changes still need `--build` (recorded in CLAUDE.md).
- Lesson: the failure mode here is the same family as skipped tests: a green or red result that doesn't describe the current code. Make "tests ran current code" structural, not a habit.

**2. The access log lost `/api/v1` from the route**
- Problem: log line said `route: "/healthz"`, test expected `/api/v1/healthz`.
- Root cause: FastAPI 0.142 resolves `include_router(prefix=...)` lazily; `scope["route"].path` is the inner route without the prefix. Only a prefix declared on the `APIRouter` itself survives (checked three wirings).
- Fix: routers declare `APIRouter(prefix=API_PREFIX...)`; shared constant in `app/api/constants.py`.
- Lesson: when a framework upgrade changes internals, test the observable output (the log line), not the wiring.

**3. `npm install` failed with ERESOLVE**
- Problem: ESLint 10 and `eslint-plugin-jsx-a11y` conflict.
- Root cause: jsx-a11y's peer range stops at ESLint 9.
- Fix: pin `eslint@9` and `@eslint/js@9` instead of `--force` or `--legacy-peer-deps`. Recorded as Decision 4.
- Lesson: resolve a peer conflict by choosing a version, not by silencing the resolver.

**4. Port 8080 was already taken**
- Problem: `web` failed to bind 8080.
- Root cause: an unrelated project's container (`infra-latex-1`) publishes it.
- Fix: host port is `${WEB_PORT:-8080}`; default unchanged; verified on 8081 and 8082. Not stopped, since it isn't ours. Recorded as Decision 3; the 8080 check is deferred to phase 14.
- Lesson: say "unverified on 8080" rather than quietly changing the target.

**5. CI "gitleaks" step failed, while reporting "no leaks"**
- Problem: `hygiene` job red; the log said "no leaks found in partial scan".
- Root cause: the gitleaks action scans `<first-commit>^..HEAD`; a root commit has no parent, so git errored and 0 bytes were scanned. A green result there would have meant nothing.
- Fix: run the pinned gitleaks CLI over the full history (same version as pre-commit). 12 commits scanned.
- Lesson: "no findings" is only evidence if you know how much was scanned. Check the byte or commit count.

**6. Two Git Bash traps**
- A large heredoc with mixed quotes failed to parse (nothing ran, so nothing was half-written). Use the file-writing tool for source files.
- `docker run -v "$(pwd -W):/repo"` was rewritten to `C:/Program Files/Git/repo`. Fix: `MSYS_NO_PATHCONV=1`.

### Phase 1

**1. Pytest failed on an Alembic warning**
- Problem: the first database test run errored at setup with a `DeprecationWarning` from Alembic.
- Root cause: `filterwarnings = ["error"]` turns warnings into failures, and Alembic wants `path_separator` in `alembic.ini`.
- Fix: `path_separator = os`. Kept the strict setting.
- Lesson: error-on-warning is a cheap tripwire for config drift; fix the cause, never loosen the filter.

**2. `str.format()` on SQL**
- Problem: building the event-kind CHECK with `.format()` would have crashed on the `'{}'::jsonb` default in the same string.
- Root cause: braces mean two things in one string.
- Fix: a placeholder token and `.replace()`. Caught on review before the first run.
- Lesson: do not template SQL with `format`; keep constants and use tokens.

**3. COPY cannot write `citext`**
- Problem: `asyncpg.InternalClientError: no binary format encoder for type citext`.
- Root cause: asyncpg's binary COPY has no codec for extension types.
- Fix: users (at most 2,000 rows) go in with `INSERT ... $2::text::citext`; everything else uses COPY.
- Lesson: bulk-load fast where the driver can, and use the boring path for the one odd type.

**4. The verifier "found" a bug in valid data**
- Problem: it reported "status in_progress has no owner" after an `unassigned` event.
- Root cause: a command writes several events at one version (`unassigned` then `status_changed`); the state between them is never observable. The verifier checked after every event.
- Fix: database rules are checked at command boundaries; field continuity and status rules still run per event.
- Lesson: a validator must check what the system can actually observe, or it rejects correct data.

**5. The simulator tried an invalid move**
- Problem: seeding stopped with `InvalidTransition: requires_approval is not allowed from awaiting_approval`.
- Root cause: my random "noise" ignored the transition table.
- Fix: noise now asks the table before acting.
- Lesson: because every command goes through one guarded table, a generator bug surfaces as an exception, not as bad data in the database.

**6. A test assumed a snapshot, not the rule**
- Problem: "all Compliance items are confidential" failed.
- Root cause: leads may legitimately switch it off later (SPEC 4.2); the rule is "confidential at creation".
- Fix: the test checks the created event, and that most are still confidential.
- Lesson: assert the rule, not what one generated dataset happens to look like.

**7. The documented migration command could not work**
- Problem: `docker compose run --rm migrate alembic revision` failed with `PermissionError`.
- Root cause: the image runs as a non-root user and nothing was mounted, so even success would have been lost with the container.
- Fix: mount `api/alembic` and run `migrate` as root (it only talks to the database).
- Lesson: run each documented command once before documenting it.

**8. My own patch scripts silently did nothing**
- Problem: `--dry-run` was "unrecognized" right after I added it.
- Root cause: a `str.replace` whose pattern had been reformatted by ruff did not match, and I did not check.
- Fix: every scripted edit now asserts the pattern matched.
- Lesson: a tool that can no-op silently needs an assertion around it.

**9. Comments bumping the version would have caused a 412 storm**
- Problem (found in review, not in code): with "every write bumps `version`", one comment makes everyone else's `If-Match` stale.
- Decision (Kumar): the version counts decision-relevant state only (title, description, type, priority, status, assignee, team, due_at, confidential, requires_approval, approval state, resolution). Comments and system events keep the current version (decision 8).
- Knock-on: unread moved from versions to event ids (migration 0002, with a data-conversion test); `changes_since` lists versioned events only; the timeline cursor is `after_event_id`; the client guard compares `(version, last_event_id)`.
- Lesson: decide what a concurrency token protects before wiring it everywhere.

**10. An invariant was only in prose (found at close-out)**
- Problem: SPEC 3.2 says `teams.key` is immutable and `work_items.key` never changes, but nothing in the database enforced it (I10).
- Fix: migration 0003, `BEFORE UPDATE` triggers; four new registry entries.
- Lesson: go through the invariants against the diff, not from memory.

**11. First large seed did not match the SPEC sizes**
- Problem: 496k events and 172k comments against SPEC 13.1's 600k and 120k.
- Fix: a `--dry-run` flag (generate and count, no database, 20 s) made tuning cheap; now 587k and 128k, 18,107 open.

**12. ruff sorted imports wrongly**
- Root cause: with `src = ["."]` it treated the local `alembic/` folder as first-party.
- Fix: `known-third-party = ["alembic"]`.

### Phase 2

**1. An un-awaited coroutine failed two unrelated tests**
- Problem: after I added `/readyz` tests, `test_every_item_of_a_demo_seed_replays_to_its_stored_state[1]` and `[2]` failed. They passed alone.
- Root cause: my scratch-database helper called `asyncio.run` inside an async test, which raised and left a coroutine un-awaited. pytest runs with warnings as errors, and the "never awaited" warning is emitted when the garbage collector runs, during some other test.
- Fix: an async variant of the helper (`ascratch_database`) that awaits everything.
- Lesson: when an unrelated test starts failing in a full run but not alone, look for a leaked warning from an earlier failure before touching the failing test.

**2. asyncpg guessed `text` for a parameter in a CASE**
- Problem: the failed-login `UPDATE` returned 500: `column "locked_until" is of type timestamp with time zone but expression is of type text`.
- Root cause: a bind parameter that appears only inside `CASE ... THEN $2 ELSE NULL` has no type context, so the driver infers `text`.
- Fix: explicit casts (`CAST(:lock_until AS timestamptz)`, and the others).
- Lesson: in hand-written SQL, cast parameters whose type the query does not reveal.

**3. My hand-written matrix was wrong once, not the policy**
- Problem: four cells of the "assigned to lead L" scenario failed: member A was expected to be allowed to block, unblock, resolve and request approval.
- Root cause: I derived that scenario from the "assigned to A" one and forgot A is no longer the assignee. SPEC 5.2 says "Member: A", meaning the assignee only.
- Fix: the expectation, not the code.
- Lesson: when a hand-written expectation disagrees with the code, re-read the SPEC cell first; the point of typing the matrix by hand is that either side can be the wrong one.

**4. A sabotage test that differed in two ways proved nothing**
- Problem: "a clause that forgets confidentiality leaks exactly the confidential items" failed.
- Root cause: my deliberately wrong clause also forgot the requester rule, so it differed from the real one in two respects.
- Fix: the wrong clause now differs in exactly one thing, and the test also checks that every extra item is confidential.
- Lesson: a negative test must change one variable.

**5. Adding CSRF broke older tests, correctly**
- Problem: `POST /_validate` and `DELETE /healthz` in the error tests started returning 403.
- Root cause: the CSRF middleware runs before routing and authentication, so an unsafe request without the token never reaches validation or the 405 check. That is the intended behaviour (decision 18).
- Fix: those tests now carry a CSRF cookie and header.
- Lesson: a security check that sits in front of everything changes what every older test sees; say so in the decision log.

**6. Broken intermediate commits**
- Problem: my first commit of the auth work contained the final `main.py`, which imported modules added by the next two commits, so it did not build on its own.
- Root cause: the script that was meant to write a reduced `main.py` failed silently, because Windows Python could not read a Git Bash `/tmp` path.
- Fix: the commits were local, so I reset and redid them with repo-relative temp files. Each now builds on its own.
- Lesson: check that every commit builds, not just the last one, and keep scratch files inside the repo directory.

**7. An I3 gap found at close-out**
- Problem: `count_open_items_owned` counts `work_items` without `visibility_clause()`.
- Root cause: it is an integrity check (a hidden item must still block a removal), but the rule says every query that counts items uses the clause.
- Fix: the exception is written into the function and proved by a test: everyone allowed to manage a team's members (its leads, admins) sees every item of that team.
- Lesson: a rule with an exception needs the exception written down next to the code, with the reason it is safe.

**8. Code generated through heredocs picked up bad escapes**
- Problem: `"\%"` in generated Python raised `SyntaxWarning: invalid escape sequence`, and large heredocs failed to parse in Git Bash.
- Fix: files are written with the Write tool; `python -W error -c "import module"` catches escapes.

**9. Known limitation, not fixed: the lock reveals which emails exist**
- Five failures lock a real account (429) while an unknown email always answers 401. Recorded in KNOWN_LIMITATIONS with what it would take (a `login_attempts` table or nginx rate limiting).

### Phase 3

**1. The first list query decorated every row before it sorted and limited**
- Problem: on the large seed (50,000 items) the default list took 20-110 ms and EXPLAIN showed a sequential scan of `work_items` and of `approvals`.
- Root cause: the query joined teams, users, the newest approval (LATERAL) and the last event onto the whole table, then sorted and cut 51 rows.
- Fix: an inner query filters, orders and limits `work_items` alone; the joins run on the 51 rows of the page (`repo/items.py` `_page` and `_item_query`). 3-12 ms for leads and members.
- Lesson: look at the plan on the big data before trusting a query that "works". The admin's unfiltered list still sorts the whole table; the expression indexes of decision 10 are the phase 12 fix.

**2. A dependency argument named `key` broke the whole app at import**
- Problem: `AssertionError: Path parameters cannot have default values` when the app started.
- Root cause: the `Idempotency-Key` header dependency had a parameter called `key`, and `PATCH /items/{key}` has a path parameter with the same name; FastAPI merged them.
- Fix: the header parameters are called `idempotency_key`.
- Lesson: parameter names in dependencies share one namespace with the path.

**3. Two guards doing the same job hid a sabotage result**
- Problem: removing the version comparison in the service left every T-STALE test green.
- Root cause: `update_item` also has `WHERE version = :expected`, which returns no row and raises the same stale error.
- Fix: none needed (the second check is intentional); the sabotage removes both and 7 tests go red (TESTING.md).
- Lesson: when a sabotage check does not go red, find out whether the test is weak or the guard is doubled.

**4. A failed login must commit its failure count before refusing**
- Problem: moving login onto `command_tx` would have undone the count, because the refusal was an exception raised inside the transaction.
- Fix: `services/auth.py` commits the count in `run_command`, then raises `Unauthenticated` outside it.
- Lesson: an exception rolls back; a command that must record a failure and still fail has to leave the transaction first.

**5. The OpenAPI test found real gaps**
- Problem: no operation had a usable id (`list_items_api_v1_items_get`) and `/readyz` had no response model.
- Fix: `generate_unique_id_function` returns the function name; `ReadyOut` model; the test now fails for any future route that lacks either.

**6. A test expectation was wrong, not the code**
- Problem: I expected the requester to be allowed `requires_approval_on`.
- Root cause: SPEC 4.2 says turning it on is for the assignee or a lead; only an ops task requester may ask at creation.
- Lesson: when a test and the code disagree, read the SPEC line before changing either.

**7. My own tooling destroyed work twice**
- Problem A: the sabotage script ran `git checkout -- file` while that file held uncommitted fixes; they were wiped, and the "sabotage" runs that followed were meaningless (they failed because the fix was gone).
- Problem B: a Python script that opened a test file with `"w"` and then crashed on a lone surrogate left it empty.
- Fix: restored from git and redone; since then, sabotage runs only on committed trees, and scripted writes go to a temp file and `os.replace`.
- Lesson: commit before sabotaging; never truncate a file you have not finished producing.

**8. The independent review found four defects and several untested paths in code I thought was done**
- NUL or a lone surrogate in `reason` was a 500; `tx.now` was taken before waiting for the lock, so `updated_at` could go backwards; `record_event` trusted the caller's version; a hidden item was locked before it was checked, so a 503 could reveal it; and the retry, DBAPIError and cancellation paths had no direct test. All fixed (decision 34).
- Lesson: ask the reviewer concrete questions with failure scenarios; it ran probes against Postgres, which found more than reading would.

**9. The large seed overwrote the demo data**
- Problem: `seed --size large --reset` TRUNCATEs what it reaches, and I pointed it at the dev database.
- Fix: demo re-seeded; CLAUDE.md now says the large seed goes into `baton_large` only (and the procedure was run once to check it works).

**10. Heredocs and quotes again** (see phase 2, problem 8): scripts with apostrophes failed to parse in Git Bash. Files and edits go through the Write and Edit tools.

### Phase 4

**1. A sabotage row from the plan did not go red**
- Problem: removing `assignee_id IS NULL` from the claim UPDATE left T-CLAIM green; removing `version = :expected` left T-STALE green.
- Root cause: the claim WHERE also has `status = 'new'`, which the winner changes in the same statement, so either clause alone stops twenty claimants; the version is compared twice (Python under the lock, and in the UPDATE).
- Fix: tests that take each clause on its own (a `new` item with an owner; `update_item` on a stale version; a stale edit that changes nothing). Now each clause turns exactly one test red (TESTING.md; decision 41).
- Lesson: when a guard is doubled, test each half directly, or "remove the guard" proves nothing.

**2. Locking first would have hidden the claim guard**
- Problem: SPEC 6.4 says lock the row, then judge in Python; SPEC 6.1 says the conditional UPDATE decides a race. Doing both means the Python check after the lock catches every loser and the UPDATE's WHERE is never what decides.
- Fix: the claim locks and checks policy but does not pre-check state; the UPDATE's WHERE is the only judge, and its rule is also a row of the transition table so `allowed_actions` and T-FLOW agree (decision 39).

**3. The test the plan asked for contradicted the rule it sits next to**
- Problem: two concurrent `request_approval` calls must give one 201 and one 409, but SPEC 6.2 requires `If-Match` on it, so the loser's stale version would be 412.
- Fix: asked Kumar; the pending-approval check runs before the version check (decision 35).

**4. A review finding I would not have found: the lock statement joined `teams`**
- Problem: a command that waited for the row lock while a transfer committed got "item doesn't exist".
- Root cause: `SELECT ... FOR UPDATE OF work_items` over a join; after the wait Postgres re-checks the whole row version against the old snapshot, and the join to the old team row no longer matched.
- Fix: lock `work_items` alone, read team and pending approval in a second statement. A test holds the lock from another connection while the team changes (`test_item_commands_edges.py`).
- Lesson: in READ COMMITTED, anything read in the same statement as `FOR UPDATE` can be stale after a wait. Lock first, read after.

**5. The review's other findings**
- A member releasing a confidential item got a 404 and the release rolled back, because the answer was rendered through the visibility filter after the change took her right to see it. Every command now renders without the filter for the person who ran it.
- `duplicate_of` showed the key of an item the viewer cannot see.
- Both were untested because no test ran any workflow command on a confidential or hidden item.

**6. My persona test modelled people as having one relationship**
- Problem: Priya was offered `withdraw` on a `new` item. The code was right: she had raised it, and a requester may withdraw.
- Fix: relationships stack (lead + requester); the expected set is the union of the table's cells.

**7. A leaked database connection failed an unrelated test**
- Problem: `ResourceWarning: unclosed transport` (warnings are errors) appeared in five approval tests after I used `await seeded.connect()` without closing it.
- Fix: use the `rows()` helper, which closes. Lesson in HANDOFF hazards.

**8. Smaller ones**
- `PlannedEvent` was used in an annotation before it was defined (found by reading, before the first run); the single-writer scanner flagged the word NOTIFY in a comment; mypy cannot infer lambdas passed to a generic racer helper (replaced by `functools.partial`); after a cancel, a second cancel is 403 for a member and 409 for a lead because policy asks who "asked" the pending request (decision 43).

### Frontend session A (phases 8 and 9, lean)

**1. The strip I built from the spec was unreadable in the queue**
- Problem: with the 560 px detail pane open, the queue column is about 490 px. One line (key, icon, title, chip, avatar, age) left the title about 90 px: "Settlement …".
- Root cause: DESIGN 4.1 describes the one-line strip; DESIGN 3.1's diagram shows the next step on a second line in that narrow column. I followed 4.1 and only saw the conflict in the screenshot.
- Fix: the strip is a grid in a container query (`.strip-link` in `globals.css`): two lines under 640 px of its own width, one line above. Same component in the inbox (wide) and queue (narrow).
- Lesson: look at the screen at the real layout before calling a component done; the spec contradicted itself and only the rendering showed it.

**2. A fade I added for hover actions hid text on rows that had no actions**
- Problem: the selected row's age read "58" instead of "58d".
- Root cause: the hover-action wrapper (with a left fade) rendered for every strip, but `AssignToMe` returns nothing when you cannot claim, so an empty wrapper faded the age on hover.
- Fix: pass `actions` only when `allowed_actions` includes `claim`. Found by zooming a 3x screenshot, not by any test.

**3. A key per call would have made the retry a duplicate**
- Problem to avoid: the obvious `useMutation` creates the idempotency key inside `mutationFn`; TanStack calls `mutationFn` again on retry, so each retry gets a new key and the server creates a second item.
- Fix: the key is made in `execute()` and travels in the variables; the form owns one key per attempt. Proof: the Playwright test lets the first POST reach the server and drops the answer; the browser retries; the server answers with `Idempotent-Replayed: true`; one item exists. The unit test pins the same key and the same `If-Match` across a network error, a 503 and the success.

**4. Smaller ones**
- `openapi-typescript` wants TypeScript 5 and the repo is on 6: an npm `overrides` entry.
- `npm run gen:api` with `${VAR:-default}` failed because npm uses cmd.exe on Windows; a small Node script replaced it.
- Python scripts I used for edits wrote CRLF (text-mode `open` on Windows); Prettier normalised it. Use `newline=''` and UTF-8, or the Edit tool.
- Quotes in a Git Bash heredoc broke a command again (third time); files go through the Write tool.
- Port 8080 is still held by an unrelated container; the stack ran on `WEB_PORT=8081`.
- A dialog that centres with `translate(-50%, -50%)` jumps when its entrance animation also sets `transform`; the overlay centres it with grid instead.

### Frontend session B (phase 10, lean)

**1. The action popover opened and closed in the same frame**
- Problem: choosing "Block" from the More menu showed nothing; the smoke script timed out waiting for the reason box.
- Root cause: Radix returns focus to the menu's trigger when the menu closes. The trigger is outside the popover, so the popover read it as a click outside and closed itself.
- Fix: the popover ignores outside interaction that lands inside the action bar (`data-popover-anchor`). Found only by driving the real page; the unit tests mount the bar but never open a menu and then a popover.
- Lesson: two Radix layers handing focus to each other need one explicit rule about who counts as "outside".

**2. The timeline did not move after my own action**
- Problem: after "Assign to me" the track and timeline still showed the old history until the next 10 s poll.
- Root cause: `useCommand` merges the returned item (new `last_event_id`) but nothing told the events query that it was behind.
- Fix: the detail watches `item.last_event_id` and invalidates the events query when it changes, for my actions and for polls alike.

**3. An edit made on an old view would have won silently**
- Problem to avoid: the description editor sent the cache's version. After a poll brought in Asha's rewrite, Priya's Save carried the new version and overwrote it with no dialog.
- Fix: the draft remembers the version it started from and sends that in `If-Match`. The Playwright stale-edit test depends on it: B saves, A saves from the old view, 412, dialog with a diff.

**4. `limit=100` on the members list was a 400**
- Problem: names for ids were all "someone". The page showed nothing wrong; the network log showed `VALIDATION_FAILED`.
- Root cause: directories allow at most 50 (`MAX_LIMIT`), events and items allow 100. I copied the wrong number.
- Fix: 50. Lesson: read the status of every request in the smoke run, not only whether the page rendered.

**5. Smaller ones**
- Events carry user and team ids, not names (assigned/unassigned/transferred); names are resolved client-side (decision 49).
- Seeded comment events carry only `data.excerpt`, not `comment_body`; the timeline showed empty comments until it fell back to the excerpt. Found in the first screenshot.
- Self-claims read "Asha assigned this to Asha" until `describeEvent` compared actor and assignee.
- `@testing-library/user-event` is not installed (and adding it is a new dependency), so the tests use `fireEvent`.
- The lint rules for React Compiler forbid reading refs during render and calling `setState` in an effect; the track's "already there" set is adjusted during render, and the live-change notice (a real subscription) carries a local disable with the reason.
- `eslint .` fails on the `.mjs` scripts (no type information for them) before and after my change; I linted `src` and `e2e`.
- Heredocs with apostrophes broke in Git Bash again (fourth time); test files went through the Write tool.

## Explain this phase

### Phase 0: foundation

1. **What was built.** An empty but real stack: Postgres, a placeholder `migrate`, a FastAPI API, a heartbeat worker, and a React page behind nginx. Tests run in containers against a real tmpfs Postgres. CI runs lint, types, tests, build, and repository hygiene (LF, no PDF/`.env`, gitleaks).
2. **Key decision: tests fail, never skip, when there is no database.** `pytest_sessionstart` in `api/tests/conftest.py` calls `pytest.exit(..., returncode=2)` if `TEST_DATABASE_URL` is missing.
3. **Alternative.** `pytest.mark.skipif` or a fixture that skips. That is how a suite reads "130 passed, 16 skipped" and still looks green.
4. **What breaks if the guard is removed.** Database tests silently disappear on a machine without the variable, and CI would stay green without proving anything. `test_pytest_fails_instead_of_skipping_when_test_database_url_is_unset` runs pytest in a subprocess without the variable and goes red.
5. **Where it lives.** `api/tests/conftest.py:22`.
6. **Second guard: prod refuses demo and fault injection.** `refuse_demo_and_faults_in_prod` in `api/app/config.py:26`. Without it, `ENV=prod` with `DEMO_MODE=true` would expose the account switcher. "Set" is read as enabled (Decision 2); tests in `tests/unit/test_config.py`.
7. **Third guard: request ids.** `resolve_request_id` in `api/app/api/request_context.py:22` only accepts `^[A-Za-z0-9._-]{1,128}$` (line 17), because the id goes into logs and headers. Remove it and a client could inject arbitrary text into a header or log line. Crashes also return problem+json with the id (`:77`).
8. **Fourth guard: LF endings.** `.gitattributes:1` (`* text=auto eol=lf`) and the CI `hygiene` job. Without them a CRLF entrypoint fails in Linux with `/bin/sh^M: not found`.
9. **nginx.** `/api/v1/stream` has `proxy_buffering off` and a 1 h read timeout, so SSE (phase 7) isn't buffered or cut off. `X-Request-ID` is forwarded, or generated by nginx if the client sent none.
10. **Not done on purpose.** No schema, auth, CSRF or CSP yet; each belongs to a later phase.


### Phase 1: schema, migrations, seed data

1. **What was built.** All of SPEC section 3 in Postgres as three hand-written migrations (tables, enums, indexes, constraints, triggers), a SQLAlchemy Core mirror, and a deterministic seed (`demo`: 600 items; `large`: 50,000 items, 586,983 events) with an independent verifier that replays every history.
2. **Key decision: the database itself rejects impossible states.** Named CHECKs, a partial unique index and triggers, so a buggy code path, a script or someone's SQL cannot write an in-progress item with no owner, two pending approvals, an approver who is the requester, an edited event, or a renamed key.
3. **Alternative.** Check these only in Python. Friendlier errors, but nothing stops the first path that forgets a check.
4. **How it is proved.** `tests/db/violations.py` lists 28 violations. Each is tested twice: rejected under that guard's own name, and, with the guard dropped inside the test transaction, accepted. So no test passes because something else happened to fail.
5. **What breaks if the main guard is removed.** Delete `owner_when_active` and 5 tests go red (done by hand, see TESTING.md); delete the trigger and events can be rewritten.
6. **Where the guards live.** `api/alembic/versions/0001_initial_schema.py:157` (`owner_when_active`), `:209` (four-eyes), `:321` (one pending approval), `:339` (append-only trigger); `0003_identity_immutable.py:44` (identity triggers).
7. **The seed cannot invent impossible histories.** `ItemSim` only takes transitions in `api/scripts/seedlib/sim.py:29` (`TRANSITIONS`), and `scripts/seedlib/verify.py:101` (`check_item`) replays the stored events independently and compares with the stored row. All 50,000 large histories pass in 17 s.
8. **Version semantics (decision 8).** `version` counts decision-relevant state only; comments and system events keep the current one, so a comment never makes another editor's `If-Match` stale. "Unread" is therefore an event id (`item_reads.last_read_event_id`).
9. **Honest gaps.** TRUNCATE is not covered by the append-only trigger (KNOWN_LIMITATIONS); the seed writes `work_items` directly (decision 7); the `COALESCE(due_at)` index question waits for `EXPLAIN` (decision 10).
10. **Numbers.** 249 API + 4 web tests, none skipped. Demo seed 1.4 s; large seed 69 s wall time.


### Phase 2: identity, sessions, authorization, visibility

1. **What was built.** Sign-in with server-side sessions, CSRF protection on every unsafe request, login throttling, an actor context read fresh from the database on every request, one pure authorization module, problem+json errors, `/readyz`, and the team, member and directory endpoints.
2. **Key decision: authorization lives in one pure module, in two forms that must agree.** `can()` answers "may this person do this to this item" (`api/app/domain/policy.py:193`); `can_view()` answers "may they see it" for one item (`:157`); `visibility_clause()` is the same rule as SQL for every query that returns or counts items (`:170`). The state-dependent cells of SPEC 5.2 are in `_can_item` (`:224`).
3. **Alternative.** Check permissions in each endpoint, or filter lists in Python after the query. Simpler to write, but each endpoint can forget, and filtering after `LIMIT` gives short pages and leaks counts.
4. **How agreement is proved.** `tests/db/test_visibility_parity.py:79` (T-VIS) compares the SQL clause with `can_view` for all 24 demo users and 300 random role combinations. `tests/unit/test_policy_matrix.py:254` checks all 2,475 cells of SPEC 5.2, typed by hand.
5. **What breaks if the main guard is removed.** Drop the confidentiality term from `visibility_clause` and farah (a Compliance member) can list confidential items she does not own: the parity test goes red and names the leaked items. Let viewers claim in `can()` and 24 matrix cells go red (both done by hand).
6. **Not visible means 404, never 403.** `can()` returns `NOT_FOUND` before it looks at the action, so a forbidden request cannot reveal that a hidden item exists. The HTTP-level proof comes with the item endpoints in phase 3.
7. **Roles are never in the cookie.** `load_actor_context` (`api/app/repo/users.py:15`) reads `memberships` on every request; the session stores only `sha256(token)` (`api/app/auth/sessions.py:33`). Removing a membership applies to the person's very next request (tested).
8. **CSRF by method, not by route list.** `CsrfMiddleware` (`api/app/api/csrf.py:31`) guards POST, PUT, PATCH and DELETE except login, and runs before authentication (`api/app/main.py:40`). The test reads OpenAPI, so later routes are covered automatically.
9. **Honest gaps.** The login lock reveals which emails exist; sessions have no absolute lifetime; membership removal is refused (not auto-unassigned) while the person owns open items until phase 4 (decision 14); the membership and login commands do not yet use `command_tx` (phase 3).
10. **Numbers.** 2,854 API + 4 web tests, none skipped, about 70 s. The auth flow was also checked end to end through nginx.


### Phase 3: work items core

1. **What was built.** Create, read, edit, list, facets and history for items; the command transaction (`command_tx`/`run_command`), `record_event`, and the idempotency layer that every later command reuses; login, logout and membership moved onto the same transaction shape.
2. **Key decision: the idempotency key lives in the command's own transaction.** The key is inserted first with `INSERT ... ON CONFLICT DO NOTHING` (`api/app/api/idempotency.py:150`). A concurrent request with the same key waits for that insert's transaction to finish, then replays the stored answer. There is no gap between "check" and "insert".
3. **Alternative.** Check for the key (SELECT), then insert it, or keep keys in Redis or a separate transaction. Two requests can both pass the check, or the key and the item can disagree after a crash.
4. **The savepoint.** The business logic runs inside `begin_nested()` (`idempotency.py:157`). A domain error (4xx) rolls back to the savepoint only, so the error answer is stored with the key and committed (`:159`; a database constraint that fires is translated at `:163`); anything else, including BUSY and any 5xx, rolls back the whole transaction, key row included, so the retry runs again.
5. **The retry.** `run_command` (`api/app/db/tx.py:80`) runs `command_tx` up to 3 times; it restarts only on SQLSTATE 40001/40P01 (`tx.py:96`), and a fresh transaction means the key insert, the events, the outbox row and the NOTIFY are all redone from nothing, never doubled. `command_tx` (`:54`) sets `lock_timeout 2s` and `statement_timeout 5s` with `SET LOCAL` (`:67-68`); timeouts become 503 BUSY with `Retry-After`.
6. **Stale writes.** The edit locks the row first (`repo/items.py:275`, `FOR UPDATE` at `:309`), judges permission, then compares the version (`services/items.py:258` raises `StaleVersion`); the UPDATE repeats `version = :expected` (`repo/items.py:373`). The 412 carries `current` and `changes_since` (the versioned events after the version the client held).
7. **History.** Every change goes through `record_event` (`db/tx.py:130`): event, `last_event_id`, outbox row and NOTIFY together, and it refuses an item whose version differs from the row (`:185`). `tests/unit/test_single_writer.py` fails if any other module writes `work_items`, `item_events` or the outbox.
8. **What breaks if the main guards go.** No `ON CONFLICT`: ten concurrent creates fail or double (T-IDEM red). No version checks: ten edits all succeed and lose each other (T-STALE red). No visibility clause: a Compliance member lists confidential items. No `COALESCE` in the cursor: items without a due date are skipped or repeated across pages.
9. **Honest gaps.** A replayed 412 can outlive its reason because `If-Match` is not in the fingerprint (decision 34, open); material edits are refused while an approval is on record until phase 4 (decision 27); an admin's unfiltered list sorts the whole table (phase 12); old idempotency keys wait for the phase 6 cleanup.
10. **Numbers.** 3,129 API + 4 web tests, none skipped, about 165 s. List 3-12 ms and facets 9-35 ms on 50,000 items for leads and members.


### Phase 4: workflow, ownership, approvals

1. **What was built.** The SPEC 4.1 transition table as data, eight commands (claim, release, assign/unassign, six transitions, transfer, request/decide/cancel approval) with one shape, approvals bound to the content by a hash, and the automatic unassign when someone leaves a team.
2. **Key decision: the claim is one conditional UPDATE, and its WHERE clause is the only judge.** `WHERE id = :id AND assignee_id IS NULL AND status = 'new'` (`api/app/repo/items.py:421-432`). A second claimant waits for the first to commit, re-checks the WHERE against the committed row and matches nothing; it then reads who won and answers 409 with the owner and time (`services/commands.py:154`).
3. **Alternative.** SELECT the item, check it is free in Python, then UPDATE. Two requests can both pass the check. Locking first narrows that, but then the Python check, not the SQL, decides, and the SQL guard is decoration.
4. **What breaks if it is removed.** Twenty simultaneous claims all return 200 and the last write wins (T-CLAIM red, both service and HTTP flavours, observed).
5. **Second key decision: an approval is bound to the content it covers.** `subject_hash` of team, type, title, description is stored when approval is requested; any PATCH that changes one of them, or a transfer, invalidates every pending or stale approved approval in the same transaction (`domain/workflow.py:341`, applied in `services/items.py`).
6. **Alternative.** Check the hash only when approving, or when resolving. The item can still sit in "approved" on text nobody reviewed, and the history lies. Remove the invalidation and T-APPROVE-RACE goes red; approve has no second hash check on purpose (decision 40).
7. **The approver's view is checked, not just the approval.** `approve` needs `If-Match` (`services/commands.py:415`): if anything changed since the lead opened the item, they get 412 with `changes_since` and nothing is approved. Remove that check and five tests go red.
8. **Rules live in one pure module.** `TRANSITIONS` (`domain/workflow.py:368`) has one row per action and starting status; `evaluate` (`:494`) adds reasons, resolutions and the approval gate; T-FLOW checks 720 cells against a table typed by hand, and the table equals the seed simulator's independent copy. Resolving a payment item without an approval is 422 `APPROVAL_REQUIRED` (`:515`).
9. **Honest gaps.** Deactivating a user has no endpoint, so nothing unassigns for it; removing someone with thousands of items is one long transaction; a removal and a claim can deadlock and one is retried; a request for approval is "already pending" before it is "stale" (KNOWN_LIMITATIONS, decisions 35-45).
10. **Numbers.** 3,325 API + 4 web tests, none skipped, about 350 s. The independent review found four defects, all fixed with tests that fail on the old code.

### Phase 5: collaboration, attention, search (time-boxed)

1. **Built.** Comments (append-only, `commented` event, same transaction), watch, read marker, `/me/attention` (five indexed queries, 10 items, count capped at 100), notifications list and mark-read, `q` search (key jump, full-text, trigram typo tolerance, top 50) and `/items/similar`.
2. **Key decision: visibility lives in the WHERE clause of every one of those queries**, before the LIMIT (`filter_clauses`, `first_items`, `count_up_to`, `list_notifications`), so a hidden confidential item cannot be listed, ranked, counted or take a place in a top 10 / top 50.
3. **Second decision: a comment never bumps `version`** (decision 8), so "unread" is an event id, and the read marker is one `INSERT ... SELECT ... ON CONFLICT` that applies visibility and `GREATEST` itself.
4. **Alternative / what it costs.** A ranked search has no cursor on purpose; "your requests" counts only activity by someone else (decision 46).
5. **Honest gap.** One test per rule only: no T-VIS property test, query-count guard, EXPLAIN, sabotage or independent review (KNOWN_LIMITATIONS, "Phase 5 was time-boxed").

### Phase 6: async worker (lean)

1. **Built.** An outbox runner (claim with `FOR UPDATE SKIP LOCKED` and a lease, backoff + jitter, dead after 8, graceful stop), notify and duplicate handlers, an SLA sweep under an advisory lock, and admin jobs + retry.
2. **Key decision: at-least-once delivery with idempotent effects,** not exactly-once machinery. A crashed runner just stops renewing its lease; the effect is guarded by `UNIQUE (user_id, event_id)`, the upsert, or the `sla_breached_at IS NULL` guard, so running twice is harmless.
3. **Details to be ready for.** Complete/fail are guarded by `status = 'pending'`; a hung handler is cut off at the lease so it cannot block the queue; notify uses `can_view` per recipient; `duplicate_suggested` carries a count, not other items' keys.
4. **Proved by** `tests/integration/test_worker.py`: 500 jobs on two runners with no double effect, a killed runner's job taken over after the lease, eight failures to `dead` and an admin retry, a double-delivered job with one notification each, two simultaneous SLA sweeps with one event per item.
5. **Honest gap.** No cleanup job, no fault injection, and the lean test set (KNOWN_LIMITATIONS, "Phase 6 was lean").

### Frontend session A: phases 8 and 9 (lean)

1. **Built.** Design tokens (both themes) with Tailwind mapped to them, self-hosted Atkinson Hyperlegible Next, a typed API client generated from OpenAPI, the shell, login, Inbox, Queue (URL filters, facets, sort, search as you type, virtualised infinite list) and the New request dialog with similar-request suggestions.
2. **Key decision: one place changes things, and it owns the retry rules.** `useCommand` creates the idempotency key when the user acts and reuses it on every retry (network error or 503 only, three times); reads `If-Match` from the item cache when the mutation actually runs; and serialises one item's mutations with `scope: item:<id>`. The server's item is merged by `(version, last_event_id)`, never older over newer (`itemCache.ts`).
3. **Alternative.** Mint a key inside each call, or read the version when the click happens. The first turns a retry into a duplicate; the second sends two queued commands with the same stale version and the second gets a 412 against the user's own first change.
4. **The screens never decide rules.** The strip shows `next_step` and an action only when `allowed_actions` has it; filters live in the URL and are validated by zod, bad values dropped one by one; lists are keyset pages, never loaded whole.
5. **Proved by.** 20 Vitest tests (`mergeItem`, cache upsert and removal, key and `If-Match` reuse across retries, queued commands in order with the new version, URL round trip) and one Playwright test that drops the first response of a create and still ends with exactly one item and a replay header. I also drove the real stack through nginx: login and return path, `j`/`k`/Enter/Esc, `c`, `/`, typo search with marked words, key jump, empty and 404 states, sign out, and a live claim.
6. **Honest gap.** No component gallery, palette, axe or large-seed scroll check, and the detail pane is a stand-in until phase 10 (KNOWN_LIMITATIONS, "Frontend session A").

### Frontend session B: phase 10, item detail (lean)

1. **Built.** The real detail screen: header, handoff track with its accessible list, an action bar rendered only from `allowed_actions`, reason popovers, approval panel and the stale-approve review, properties (optimistic priority and watch), a description editor with a local draft, a timeline with filters, an unread divider and an optimistic composer, and 10 s polling.
2. **Key decision: the editor sends the version it started from, and a 412 is sorted by field overlap.** `lib/rebase.ts` reads the field names out of `changes_since` events and compares them with the fields I sent. Disjoint: resend on the server's version with a new idempotency key and say what was kept. Overlap, or no `changes_since`: the ConflictDialog shows the word diff and two choices. Esc decides nothing and keeps the draft.
3. **Alternative.** Send the cache's version (no conflict ever shows, because a poll hides the other person's edit, so the save silently overwrites it), or always show the dialog (every unrelated change interrupts the user).
4. **The UI knows no rules.** `barModel` maps `allowed_actions` to buttons and a test feeds it every pair of actions to prove it never offers one the server did not allow. A lowered priority asks for a reason only because the server answered `REASON_REQUIRED`; resolve without approval offers "Request approval" only because the server answered `APPROVAL_REQUIRED`.
5. **Proved by.** 18 new Vitest tests (rebase plans and resend/limit/passthrough; the dialog's three outcomes producing the right PATCH with the right `If-Match` and a new key; `allowed_actions` driving buttons) and two Playwright tests with two browser contexts: a claim race released together (exactly one "Assigned to you", the other "took PAY-nnn", both screens end on the same owner) and a stale description edit (conflict dialog with the diff, then "Save my version"). By hand: claim, block with reason, unblock, priority with the reason popover, comment, resolve, stale approve and the draft banner, with screenshots in four statuses, light and dark.
6. **Honest gap.** Polling, not SSE; most properties read-only; the disjoint-edit, approve-after-edit and live scenarios have no Playwright test (KNOWN_LIMITATIONS, "Frontend session B").

### Phase 14: submission readiness (lean, 90 minutes)

1. **Built.** README (quick start, demo logins, tests, repo map, assumptions), Top 5 decisions, ARCHITECTURE (diagrams and scaling path), the CB1-CB8 test map and the not-tested list, an ordered KNOWN_LIMITATIONS, a 5-minute DEMO_SCRIPT, and SPEC text corrected to say what the code does (decision 50). No feature work.
2. **Problem: CI had been red for two pushes.** *Root cause:* `eslint` runs type-aware rules on every file, and the Node scripts in `web/scripts/*.mjs` (added in the frontend sessions) are outside `tsconfig`, so ESLint refused to lint them. *Fix:* the same `disableTypeChecked` override the `*.config.js` files already had, now also for `scripts/**/*.mjs` (`web/eslint.config.js`). *Lesson:* run `gh run list` after every push; I had only run `tsc` and `eslint` on files I touched, and the CI failure was in a file I had not.
3. **Problem: the clean-clone run needed the documented port fix.** *Root cause:* another local project holds 8080, so `docker compose up --build` stops at the `web` container ("port is already allocated"). *Fix:* none needed in code; README says `WEB_PORT=8081`. I ran the check on 8082 because 8081 was my dev stack. *Lesson:* run the checklist literally; the failure is the proof the one-line fix is the right documentation.
4. **Problem: two test runs on one test database.** *Root cause:* I started the clean-clone `api-test` in a background shell that I thought had died; it had not, so a second run started against the same `db-test`. HANDOFF already says never to run two sessions (the db tests rebuild the schema). *Fix:* killed both, tore the stack down, ran it once. *Lesson:* check `docker ps` before starting a test run, and do not start a long command twice.
5. **Explain this phase.** A reviewer should be able to clone, run one command, sign in, and reproduce six behaviours in five minutes, and then see every claim in the docs point to code or a test. Where the code is behind the plan (polling, missing screens, no performance numbers) the docs say so first, in KNOWN_LIMITATIONS and as "not built in v1" markers in the SPEC.


### Phase 11: management visibility (lean, 90 minutes)

1. **Built.** `GET /stats/teams` and `GET /decisions` through `visibility_clause()`, the dashboard and decision log screens, and the notification bell (unread count capped at "20+", popover grouped by item, mark read). Decision 51.
2. **Key decision: every dashboard number uses the queue's own definition**, so the figure and the list behind it cannot drift. Open, overdue and unowned are the same expressions the list filters use; a test fetches the member's and the lead's dashboard, then lists the same filters as each of them and compares. "Going quiet" is shown but not linked, because the queue cannot filter it yet and a link to a different list would make the number a lie.
3. **Alternative.** Materialised counters per team (fast, but they would need their own visibility rule per viewer, which is the leak the brief warns about), or computing counts in Python after loading items (unbounded, and one forgotten filter shows a confidential item).
4. **Problem: the dashboard's big numbers rendered at 14 px, not 32.** *Root cause:* `cn()` is `tailwind-merge`, which does not know Baton's custom type sizes (`text-figure`, `text-meta`...) and reads them as colours, so `cn("text-figure", "text-ink")` keeps only `text-ink`. *Fix:* `clsx` for that element. *Lesson:* I only saw it by looking at the screenshot; types, lint and tests were all green. The same trap sits in older `cn(...)` calls (a custom size next to a colour inherits 14 px); fixing it globally would change existing screens, so it is written down instead.
5. **Problem: a decision test failed with 400.** *Root cause:* closing an item needs a `resolution` as well as a reason (SPEC 4). *Fix:* the test sends both. *Lesson:* read the workflow table before writing a test against it.
6. **Explain this phase.** Management asked "who owns it, what needs attention, what is forgotten, why was it decided". The dashboard answers the first three per team, the decision log answers the fourth with the reason as a quotation, and both show only what the viewer may see, so a lead and a member of one team see different numbers on purpose and each number opens the exact list it counts.


### Phase 7: live updates over SSE (60 minutes, branch `phase-7-sse`)

1. **Built.** `GET /api/v1/stream` (Server-Sent Events) fed by one plain asyncpg `LISTEN item_changes` / `LISTEN notifications` connection per API process, an in-process hub with a bounded queue per client, a visibility check per client, a 500-event replay ring for `Last-Event-ID`, `resync` when replay is impossible, and one `EventSource` in the web shell with 250 ms per-item coalescing and 10 s polling after three failed connects. Decision 52.
2. **Key decision: the notice carries `{key, version, event_id}`, never content.** The browser refetches the item through the merging query (I13), so the stream cannot leak a field and the version guard still decides what is newest. `NOTIFY` is delivered only on commit, so a rolled-back command sends nothing (tested with a real `record_event` followed by a raise).
3. **Alternative.** Replay from `item_events` by id (the BUILD_PLAN design): exact and restart-proof, but it needs a visibility-filtered range query per reconnect and commit-ordered ids. I kept an in-memory ring and a `resync` fallback: the cost of the shortcut is a refetch, not a wrong screen.
4. **Problem: a reconnect can miss an event.** *Root cause:* event ids come from an identity column, assigned at insert, so a transaction that inserted first can commit after one that inserted later; a client that has seen the higher id asks for "after that" and never gets the lower one. *Fix:* none in code; every (re)connect resets the buffer and sends `resync`, and the bell's 20 s poll covers notifications. Written up in KNOWN_LIMITATIONS 1. *Lesson:* a sequence is not a commit order; if replay must be exact, use a commit-ordered cursor.
5. **Problem: the stream was only mounted in the item pane, so lists never moved.** *Root cause:* the first version lived in `ItemDetail`. *Fix:* one `EventSource` for the whole signed-in app in `Shell`. *Lesson:* where a subscription is mounted decides what it can update.
6. **Explain this phase.** Two people look at the same queue; when one acts, the other sees it within about two seconds without asking. The server tells every client "item X is now at version V" if that client may see X; the client decides whether it already knows that and refetches if not. If the stream breaks, the client falls back to polling, so the product never depends on it for correctness.

### Frontend extras (palette, jobs, team settings, inline edits, 375 px) and the final pass

1. **Built (branch `fe-extras`, merged with no conflicts).** Command palette on `Ctrl/Cmd+K` (cmdk; actions come from `allowed_actions`, Jobs for admins only), the admin Jobs screen with retry for dead jobs, team settings (members, roles, hand-back confirmation when someone is removed), inline editors for title, due date, type, confidential and requires-approval on the shared `useSaveFields`/rebase path, and a 375 px layout with bottom navigation.
2. **Problem: custom text sizes silently disappeared.** *Root cause:* `cn()` was plain `tailwind-merge`, which does not know `text-figure`, `text-meta`... and reads them as colours, so `cn("text-meta", "text-pencil")` kept only the colour (phase 11 had already worked around it with `clsx` on the dashboard figures). *Fix:* `extendTailwindMerge` with Baton's sizes and weights, and a Vitest test that fails on the old `cn` (3 of 4 cases red, checked). *Lesson:* a bug in a shared helper shows up as "a style is missing somewhere"; fix the helper and prove it with the failing test, not each call site.
3. **Problem: a notification stayed unread after the item was opened from the queue.** *Root cause:* only the bell's popover marked notifications read; opening the same item any other way only wrote `item_reads`. *Fix:* `POST /items/{key}/read` also marks that person's notifications for the item read, in the same statement batch as the read marker, and the pane refreshes the bell. A new API test fails without it (checked) and shows other items and other people's rows are untouched. *Lesson:* "seen" is one fact; put the rule where the fact is recorded, not in one screen.
4. **Problem: a Playwright test broke after the merge.** *Root cause:* the new title pencil is also a button named "Edit", so `getByRole("button", { name: "Edit" })` matched two elements (substring match). *Fix:* `exact: true`. *Lesson:* when you add a control, search the tests for the label it shares.
5. **Problem: the host `node_modules` lacked `cmdk`,** so `tsc` and Vitest failed on the merged tree while the lockfile was right. *Fix:* `npm ci`. *Lesson:* a red check right after a merge is not always the merge; look at the error text first.
6. **Final pass facts.** axe (`web/e2e/axe.spec.ts`) found no serious or critical issue on login, inbox, queue, item detail and dashboard (colour contrast is "incomplete" on two of them: axe could not decide). The demo script was run literally with two browsers; its wording changed in several places (for example the description edit needs a lead or the owner, not any member; a double click sends one request; Resolve opens a form and the refusal is a toast).
7. **Explain this phase.** Nothing new was invented: the palette, jobs, team settings and inline edits are thin screens over commands that already existed, so each one inherits the same checks (policy, version, idempotency key per changed request). The last hours went into making the claims true: a stack from a fresh clone, a script run by hand, and docs that say what is and is not there.
