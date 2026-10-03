# Known limitations

What Baton does not do, or does only partly, and what it would take. The out-of-scope list in
SPEC section 15 is added in phase 14; this file starts with limitations found while building.

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

About 20 ms on 50,000 items, on every page. It needs the expression indexes of decision 10, which
phase 12 measures (ENGINEERING_DECISIONS 32).

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
