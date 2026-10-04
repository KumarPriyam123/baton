# Known limitations

What Baton does not do, or does only partly, and what it would take. The first part is the summary,
ordered by how much it matters to someone using or judging the system; the second part is SPEC section 15
(out of scope on purpose); the third is the detail found while building, in the order it was found.

## Summary, most important first

| # | Limitation | Effect | What it would take |
|---|---|---|---|
| 1 | **Live updates are SSE with a per-process replay buffer** (decision 52). Replay after a reconnect comes from an in-memory ring of 500 events, not from `item_events`, and event ids are not commit-ordered | A restart of the API, or a gap of more than 500 events, makes every client refetch what it shows (`resync`) instead of replaying; an event whose id is lower than one already seen can be missed on a reconnect (the next `resync` or the 20 s bell poll fixes it); several API processes each hold their own buffer | Replay from `item_events` by id with a visibility filter (the BUILD_PLAN design); no "N items changed" bar on the list yet |
| 2 | **Two screens are not built**: team settings and the jobs page (the dashboard, decision log and notification bell exist since phase 11, lean: see "Phase 11 was lean") | Members are managed only through `/teams/{key}/members`; dead jobs only through `GET /admin/jobs` | Two screens against the generated client |
| 3 | **Most properties are not editable in the UI**: title, type, due date, confidential, requires-approval (priority, watch and description are) | The API supports every edit (`PATCH`); users cannot do them from the item pane | Inline editors reusing `useSaveFields` and the rebase logic |
| 4 | **Performance was measured once, read-only, by a reviewer, and the admin paths miss SPEC 13** (`docs/PERFORMANCE.md`): admin queue, facets and dashboard are sequential scans; admin search on a common word took 783 ms in the database; the attention endpoint's wall p95 was over target on a loaded host. Commands, the HTTP layer and the worker were not measured; `bench.py` was not run | The SPEC 13 targets hold for members and leads on the queue and point reads, not for admins; 10x numbers are extrapolated | Index and search changes under "Findings from the independent review" (M3-M5, L5), then run `bench.py` |
| 5 | **No cleanup job and no fault injection**: idempotency keys, expired sessions and `done` outbox rows are never deleted; `FAULT_NOTIFY_FAIL_RATE` is read but unused | Tables grow; an old idempotency key still replays | The hourly job from SPEC 9 under an advisory lock (a few handlers, a few tests) |
| 6 | **Test coverage gaps** (TESTING.md, "What is deliberately not tested"): three Playwright scenarios, no axe or screen-reader check, no 375 px layout, no sabotage runs after phase 4, `--profile e2e` Compose service missing | Browser behaviour beyond login, inbox, queue, create and item detail is unproven; phones are not designed for (A10 says "usable") | A Playwright service in `compose.test.yaml`; the remaining plan scenarios; `@axe-core/playwright` on each screen |
| 7 | **No deactivate-user endpoint.** Accounts are deactivated in the database, and a deactivated user keeps the items they own | Items can be stuck with someone who has left until a lead reassigns them | An endpoint calling `unassign_owned_items` per team in one transaction (ENGINEERING_DECISIONS 44) |
| 8 | **Authentication is real but basic**: no absolute session lifetime, failed logins have no time window, the lock reveals which emails exist after five failures, no rate limiting at nginx | A kept-busy stolen cookie never expires; credential stuffing is only slowed per account | `absolute_expires_at`; a `login_attempts` table keyed by email hash; nginx `limit_req`. SSO replaces all of it in production (A1) |
| 9 | **Append-only is for UPDATE and DELETE, not `TRUNCATE`** | Whoever owns the table can still erase history | Run the app as a role without TRUNCATE; keep the owner role for migrations and the seed |
| 10 | **Two small consistency windows**: a command is authorised with the roles read at request start (a 2 s window); a replayed 412 can outlive its reason | A just-demoted lead's last in-flight edit can succeed; a client that reuses a key after a rebase is told the old answer | Re-read the role after the lock; include `If-Match` in the idempotency fingerprint (the web client already uses a new key per changed request) |
| 11 | **Names for ids and long lists are approximate**: a team has its first 50 members in pickers; a former member who never acted reads "someone"; the user directory scans with `ILIKE`; `changes_since` is capped at 100 | Large teams and long histories degrade quietly instead of failing | Server-side names in event payloads; `pg_trgm` index on users; paged pickers |
| 12 | **Port 8080 must be free** for the default setup | `docker compose up` fails with "port is already allocated" | `WEB_PORT=8081` in `.env` (README). Hit on the author's machine during the clean-clone check |

CI at submission: see the last entry of this file ("CI status").

## Out of scope for v1 (SPEC section 15)

Each is a decision, not an oversight: SSO; email and Slack notifications (the outbox is built so another handler can
do it); attachments; @mentions; editing or deleting comments (history is append-only, A8); bulk actions;
searching comments; custom workflows per team (the transition table is data in `domain/workflow.py`, so a new
status is one place plus a migration for the enum); business-hours SLA calendars; real-time co-editing of text
(conflicts are detected and shown, not merged); materialized dashboard counters (counts are queried live
through the visibility clause); multi-region.

## Detail found while building

## History is append-only for UPDATE and DELETE, not for TRUNCATE

`item_events` has a trigger that rejects UPDATE and DELETE (SPEC 3.2, CLAUDE.md I9). A
statement-level `TRUNCATE` is not covered, and `scripts/seed.py --reset` uses it to wipe the
demo data. Anyone connected as the table owner can therefore still erase the audit trail.

- **Why it is left open:** the seed's reset needs it, and the app never issues TRUNCATE.
- **What it would take:** run the API and worker as a role that owns nothing and has no TRUNCATE
  privilege (`REVOKE TRUNCATE ON ALL TABLES`), keeping the owner role for migrations and the seed;
  optionally a `BEFORE TRUNCATE` statement trigger that the seed bypasses explicitly.

## Login lock reveals which emails have an account (after five failures)

Five wrong passwords lock a real account, and the sixth attempt answers 429. An unknown email has no
row to lock, so it keeps answering 401. Someone who tries an email six times can tell the two apart.
SPEC 5.4 asks only that the message be the same for an unknown email and a wrong password, which it is.

- **What it would take:** a `login_attempts` table keyed by a hash of the (normalised) email, so
  unknown emails are throttled exactly like real ones; or rate limiting at nginx by client address.

## Sessions have no absolute lifetime

A session lasts as long as it is used at least once every 12 hours (SPEC 5.4 specifies idle expiry
only). A stolen cookie that is kept busy never expires until the user logs out or is deactivated.

- **What it would take:** an `absolute_expires_at` (for example 7 days) checked next to `expires_at`.

## Failed logins have no time window

The counter resets on a successful login or after a lock expires, not after a quiet period: four
failures a month ago plus one today lock the account.

- **What it would take:** store the time of the first failure of the current run and reset the count
  when it is older than a window.

## Deactivating a user does not unassign their items yet

SPEC 4.3b lists deactivation next to removal and demotion, but SPEC 11 has no endpoint to deactivate
anyone (accounts are deactivated in the database). Removal and demotion unassign the person's open
items (ENGINEERING_DECISIONS 44); a deactivated account keeps what it owns.

- **What it would take:** the deactivate command calls `commands.unassign_owned_items` for each of
  the person's teams in the same transaction.

## Removing someone who owns thousands of items is one long transaction

The unassign runs in the membership command's transaction, 100 items per round (I8), and an item
takes about eight statements. Someone with a few hundred open items is fine; thousands would approach the
5 s statement and 2 s lock timeouts for the other commands waiting on those rows.

- **What it would take:** a worker job that unassigns in batches, with the membership change marking the
  person "leaving"; costs the "same transaction" guarantee, so only worth it at that scale.

## The user directory is open to every signed-in user and uses a sequential scan

`GET /users?q=` returns name and email of active people to anyone signed in (an internal directory,
SPEC 11), and matches with `ILIKE`, which scans the `users` table. That is fine at a few thousand
rows; a `pg_trgm` index would be the next step. The demo-account list recognises personas by having no
digit in the email (`aarav.gupta17@...` is generated, `priya.lead@...` is not).

## Old idempotency keys still replay until the cleanup job exists

SPEC 6.3 says keys expire after 24 hours, which the phase 6 cleanup job enforces. Until then a key
is honoured for as long as its row exists. Browsers create a fresh random key per action, so this
only matters for a client that reuses keys.

## An admin's unfiltered item list sorts the whole table

The author measured about 20 ms on 50,000 items; the independent review measured 47-76 ms (sequential
scan, deep pages slower than the first). It needs the expression indexes of decision 10 (see review M3 below).

## `changes_since` shows at most the 100 oldest changes

A client that was offline through more than 100 versioned changes gets the first 100 plus the item as
it is now, which is what the conflict dialog needs. It is not a history view (use the events endpoint).

## A command is authorised with the roles read when its request began

`current_actor` reads memberships once per request, before the command transaction. If a lead is
demoted while their PATCH waits (at most the 2 s lock timeout), that PATCH is still judged with the
old role. The next request sees the change (SPEC 5.3 holds). Re-reading the role after the lock would
close the window for one extra query per command.

The one place where this would break SPEC 4.3b is closed: whoever becomes an owner (claim, assign,
reopen to the previous owner, transfer) has their role read again under a share lock on their
membership row (ENGINEERING_DECISIONS 42), so a removed person cannot end up owning an item.

## A replayed 412 can outlive the reason it was a 412

An idempotency key stores a 412 for 24 h and `If-Match` is not part of the fingerprint
(ENGINEERING_DECISIONS 34). A client that rebases and re-sends under the same key is told the old
answer. The web client must create a new key whenever the request changes.

## A removal and a claim can deadlock, and one of them is retried

A command that makes someone the owner locks the item row and then reads their membership under a
share lock; removing or demoting that person locks the membership row and then their items
(ENGINEERING_DECISIONS 42). The two orders can deadlock. Postgres picks a victim and `run_command`
restarts it (up to three times, then 503 BUSY). It is rare and always resolved; the alternative, one
global order, would mean locking memberships before every item command.

## A second approval request is "already pending" even if the caller's view is stale

SPEC 6.2 requires `If-Match` on `request_approval`. When an approval is already waiting, the answer is
409 `APPROVAL_ALREADY_PENDING` before the version is compared (ENGINEERING_DECISIONS 35), so a client
that was also out of date learns about the wait first and about the other changes on its refetch.

## Cancelled and invalidated approvals share one reason column

`approvals.invalidated_reason` also holds why a request was cancelled by the system ("transferred",
"Removed from Payments"). The SPEC 3.2 table has no separate column; the events carry the same text.

## Phase 5 was time-boxed: what is NOT tested

Phase 5 has one test per rule or endpoint (visibility on every new read path, idempotency on every new
write, each attention rule, the search rules). Deliberately left out, so nobody reads green as proof:

- **No T-VIS property test** over all demo users for search, facets, attention, similar and
  notifications. The visibility rule is in the WHERE clause of each query and the phase 3 HTTP
  confidentiality test now covers search, attention and notifications for one confidential item, a
  member and a lead; that is an example, not a proof over every persona.
- **No query-count guard** on attention or the list (BUILD_PLAN asked for one), and no `EXPLAIN` of the
  five attention queries on `baton_large`. Each is written to hit an existing index
  (`work_items_assignee_open_idx`, the "needs an owner" and going-stale indexes, `approvals` by item),
  but that is by reading, not measured. "Your requests" can scan all of one requester's items until it
  finds ten unread ones.
- **No sabotage checks** (for example: drop `visibility_clause` from one section and see what goes red)
  and **no independent review** of phase 5.
- **No test of search ranking edge cases:** stop words, quotes or `-` in `websearch_to_tsquery` input,
  very short queries and the trigram threshold (a 3-letter typo may not match). Only the three rules the
  plan names are tested: key jump, title above description, one typo.
- **No concurrency test** of two comments or two reads on one item at once (the comment takes the item
  row lock like every command, and read is one statement, but neither is exercised under load).
- **Not tested:** `GET /items/similar` excluding resolved and closed items; the 50-result cap of a
  search; notification paging past hidden rows; the 412 of a stale client around a comment.

Known and not fixed: the full notification list (`unread=false`) has no index of its own, since SPEC 3.2
lists only the partial unread one, so it reads and sorts one user's rows. Fine while the cleanup job
(phase 6) keeps them bounded; a `(user_id, id DESC)` index is the fix if `EXPLAIN` asks for it.

## Phase 6 was lean: what the worker does not do, and what is not tested

- **No cleanup job.** Idempotency keys older than 24 h, expired sessions and `done` outbox rows are never
  deleted, so those tables grow and old keys still replay (see the entry above). `FAULT_NOTIFY_FAIL_RATE`
  is read by `Settings` but nothing uses it yet.
- **A handler's work and the `done` mark are two transactions.** A crash between them runs the job again;
  every handler is idempotent, so the result is the same, but it is the at-least-once cost.
- **A job runs sequentially inside a batch of 50 with one lease for all of them.** Fast handlers never
  come near 60 s; a slow batch could see its later jobs taken over by another runner while still queued
  here (they would then run twice, harmlessly).
- **The SLA sweep runs on every worker every 60 s.** The advisory lock only stops two at once; a worker
  that waits for the lock does not queue. Cheap, and correct.
- **Notify judges visibility against the item now,** not at the time of the event. Someone who lost access
  between the two is not told; someone who gained it is.
- **Not tested (time box):** a stopped worker catching up through real `docker compose` processes (checked
  by hand: two workers start, one SLA sweep ran, both stop cleanly on SIGTERM), SIGTERM mid-batch release,
  the 50-job batch edge, fan-out to a team's whole watcher list, a lost-lease runner finishing late, the
  advisory-lock-busy path of the sweep, the retry endpoint's idempotency key, and the duplicate finder's
  threshold. No property tests, sabotage checks or independent review.

## Frontend session A (phases 8 and 9) was lean: what is missing, and what is unverified

**Not built (by the time box):** the `/dev/ui` component gallery and the DESIGN 8 component matrix (Popover,
Drawer, Tabs, Tooltip, DiffView, HandoffTrack, Sparkline, Textarea-with-Preview as a component); the command
palette; the CI check for raw hex and for generated-types drift (the rule holds by grep today; nothing
enforces it); the dashboard, decisions, teams, jobs and notification screens (the rail has no links to
them); the "N items changed, Refresh order" bar (needs SSE, phase 10); the compact-density toggle; the
offline banner; the `?` shortcut help; the Watch quick action on strips; a searchable team combobox
(native select instead); a Playwright service in `compose.test.yaml` (the e2e test runs on the host with
`BASE_URL` and `PW_CHANNEL=chrome`; CLAUDE.md's `--profile e2e` command does not exist yet).

**Approximate on purpose:** the inbox's "Show all" opens the nearest queue filter. "Needs your approval"
becomes `status=awaiting_approval` (all of them, not only those I can approve) and "Going quiet" becomes
my in-progress and blocked items sorted by update time; the queue has no filter for either rule.
Narrow screens (under 768 px) are not designed: the rail stays an icon column and the strips use the
two-line layout; touch targets are not 44 px.

**Unverified:** the queue on the large seed (1,000+ rows, smooth scroll) was not run; the list is
virtualised and keyset-paged, and was checked only on the demo data. Dark mode and 375 px
screenshots were not reviewed (one dark queue screenshot was looked at). No axe run. The ARIA live region
exists but nothing writes to it yet. Two React Compiler lint warnings remain (`react-hook-form` `watch` and
TanStack Virtual are not memoisable; harmless, the compiler skips those components).

**Behaviour to know:** the detail pane is a stand-in (decision 48; replaced in session B, below). Changing a
filter keeps the previous rows on screen, dimmed, until the new ones arrive. The e2e test and my checks left
a few `E2E ...` requests in the dev database.

## Frontend session B (phase 10) was lean: what is missing, and what is unverified

**Not built:**
- **SSE (phase 7).** Built later, see decision 52. What is still missing: no "N items changed, Refresh order" bar on the list (lists refetch wholesale).
  Before decision 52 live updates were 10 s polling (measured 9 s). Lists still refetch wholesale, so a
  list page older than a just-applied mutation can show old data until the next refetch.
- **Editing most properties.** Only priority (optimistic) and watch are editable inline, and the
  description. Title, type, due date, confidential and requires-approval have no editor even though
  `allowed_actions` offers them; the Properties rows are read-only.
- **Shortcuts on the detail:** `m` (comment box) only. `a`, `p` then a digit and `s` are not wired.
- **Names for ids.** Event data names people and teams by id. Names come from actors, the item's people
  and the team's first 50 members; a former member who never acted on the item shows as "someone".
  A team with more than 50 members is not paged for the assignee picker.
- **Watchers list.** `ItemOut` has only `watching` for me, so Properties shows my own watch state, not
  the list DESIGN 4.4 describes.
- **Approval details.** The panel's text comes from `item.approval` plus the latest approval event: the
  server sends no approval note, hash or version. "Approved against which version" is not shown.
- **Drawer at 1024-1279 px** reuses the session A overlay; not re-checked. No 375 px layout, no axe, no
  notifications, dashboard, decisions, teams or jobs screens, no command palette (as agreed).
- **Not run:** the third, fourth and fifth Playwright scenarios of BUILD_PLAN phase 10 (disjoint edit,
  approve after edit, live) have no Playwright test. Disjoint edit is a unit test. A throwaway script
  drove approve-after-a-priority-edit (412, "Show changes", "Approve this version", approved) and a live
  priority change (visible after 9 s, no reload) and I looked at the screenshots; **approve after a
  *description* edit was not driven** (the edit withdraws the approval, so Approve disappears after the
  412; the withdrawn panel was seen, but not that exact sequence). No sabotage checks and no
  independent review, by instruction.

**Approximate on purpose:** the stale-approve check uses the version in the cache when the button is
clicked, so an approver who already sees a change (the poll arrived first) is not stopped. The server still
refuses an approval whose content changed (the edit withdraws it). The primary action is the first
`allowed_actions` entry in a preference order per `next_step.kind` (`actions.ts`); a team that wants a
different default changes that table, not the workflow.

**Unverified:** dark mode was reviewed on two screens (blocked, awaiting approval) only; the other
statuses in dark, the 1024-1279 px drawer, long timelines (more than 100 events, "Show earlier events")
and a track with the "+N" collapsed middle were not looked at (the collapse has no test). Screen reader
output was not heard; the `aria-live` regions are written to (polite for live changes, assertive for the
conflict dialog) and the track has an `ol` with the same facts, but nothing was run in a reader.
Two React Compiler lint warnings from session A remain.

## Phase 11 was lean: what the management screens do not do

`GET /stats/teams`, `GET /decisions`, the dashboard, the decision log and the notification bell are built
(decision 51). Left out by the 90-minute box, so nobody reads them as more than they are:

- **Dashboard figures are a subset of SPEC 7.** Built: open by status and priority, overdue, unowned, going
  quiet, the five oldest open items, the busiest eight owners. **Not built:** median age, the 14-day
  created-versus-resolved sparkline, "awaiting approval" as its own column beyond the status count.
- **"Going quiet" is a number, not a link.** The queue has no filter for "no activity for 72 h"; linking to
  the nearest preset would open a list that does not match the number. Every other figure links to a queue
  filter that returns exactly that count (`overdue=true`, `assignee=none` with the four open statuses, one
  status, one priority), and a test compares them.
- **The dashboard reads all open items of the teams a person can see on every request** (one aggregate and
  two small queries, 4 statements). No index was added and it was not run on `baton_large`; at 50,000 items
  with most of them open it would scan them. A partial index on open items, or the materialised counters of
  SPEC 15, are the fixes.
- **Decisions about a confidential item are judged against the item now**, like notifications: someone who
  lost access sees none of them, someone who gained it sees all of them.
- **The bell polls** (the unread count every 20 s, the list while open); it does not use the stream.
  The count is capped at "20+" (it fetches 21). Opening an item from the popover marks that item's
  notifications read; opening it any other way (the queue, a link) does not.
- **Decision wording uses names the page knows.** A transfer shows team names from `GET /teams`; a person
  who is only an id in the event data would read "someone".
- **Tests (time box):** one API test per endpoint for visibility, plus paging and filters, and a Vitest test
  of the badge cap. No Playwright, no T-VIS property test over the new endpoints, no query-count guard
  for the dashboard, no axe run, no 375 px layout, no sabotage run and no independent review.
- **The popover's focus and keyboard behaviour is Radix's**; it was not driven by keyboard here.

## Findings from the independent review (Medium and Low)

Source: `docs/notes/review.md`, written against `57662ad`, triaged at `552e526`. No Critical or High
findings, so none were fixed in code. "Confirmed" says what was checked; the rest is as the reviewer
reported it.

- **M1. `duplicate_suggested` reveals that a hidden item exists** (confirmed by reading). `similar_to`
  has no visibility filter and the event carries `count` and `top_score`, which the requester can read.
  A user raising a request to a team they are not in can probe titles. Fix: filter by the requester's
  visibility or drop the numbers; add a T-VIS case.
- **M2. No Content-Security-Policy, and Markdown renders remote images** (confirmed). SPEC 5.4 promises
  a strict CSP; `nginx.conf` sets none. A comment with `![](https://host/x.png)` makes every viewer's
  browser fetch it. Fix: CSP header plus an `img` override in `Markdown.tsx`.
- **M3. Queue pagination is not index-ordered** (reported, EXPLAIN on `baton_large`). Admin: sequential
  scan, deep pages slower; members: heap scan of everything visible. `work_items_team_queue_idx` cannot
  give the sort order. Fix: partial indexes matching the sort, per-team `LATERAL` top-N; correct SPEC 3.2.
- **M4. Admin search on a common word is a sequential scan** (reported): 783 ms for `refund`. Fix: rank a
  bounded candidate set.
- **M5. The documented typo `refnd` finds nothing** (reproduced: `word_similarity` 0.5 < 0.6 threshold,
  0 rows). The test uses a longer word. Fix: lower `pg_trgm.word_similarity_threshold` for the statement
  and test `refnd`.
- **L1.** Rapid priority edits can 412 against the user's own earlier edit (`useSaveFields` reads the
  version at call time). **L2.** An older poll response with an equal `(version, last_event_id)` can flip
  `watching` back for up to 10 s. **L3.** Opening an item marks read up to the server's current
  `last_event_id`, which can include a comment not yet shown. **L4.** An out-of-range cursor priority or an
  integer cursor above 2^63-1 probably returns 500, not 400 (reading only). **L5.** The dashboard's "five
  oldest" has no `(team_id, created_at)` index: 178 ms for an admin. **L6.** `sla_breached_at` is never
  cleared, so an item whose due date moves out still shows "Overdue" in some places.
- **L7** (docs and SSE disagree) was obsolete when triaged: SSE has since merged.

## CI status

At tag `submission-candidate-1`, CI (api, web, hygiene) is green on `main` (run 37149574243). The `web` job had been red since frontend session A because ESLint could not type-check `web/scripts/*.mjs`; fixed in `web/eslint.config.js`. The Playwright tests are not run in CI.
