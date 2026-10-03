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

## Membership removal is refused while the person owns open items

SPEC 4.3b wants those items unassigned automatically. Until the workflow exists (phase 4), a lead must
reassign or release them first (ENGINEERING_DECISIONS 14).

## The user directory is open to every signed-in user and uses a sequential scan

`GET /users?q=` returns name and email of active people to anyone signed in (an internal directory,
SPEC 11), and matches with `ILIKE`, which scans the `users` table. That is fine at a few thousand
rows; a `pg_trgm` index would be the next step. The demo-account list recognises personas by having no
digit in the email (`aarav.gupta17@...` is generated, `priya.lead@...` is not).

## Old idempotency keys still replay until the cleanup job exists

SPEC 6.3 says keys expire after 24 hours, which the phase 6 cleanup job enforces. Until then a key
is honoured for as long as its row exists. Browsers create a fresh random key per action, so this
only matters for a client that reuses keys.

## Material edits are refused while an approval is on record (until phase 4)

See ENGINEERING_DECISIONS 27. Editing the title, description or type of an item that has a pending
or approved approval is a 409 for now, instead of invalidating the approval.

## An admin's unfiltered item list sorts the whole table

About 20 ms on 50,000 items, on every page. It needs the expression indexes of decision 10, which
phase 12 measures (ENGINEERING_DECISIONS 32).

## `changes_since` shows at most the 100 oldest changes

A client that was offline through more than 100 versioned changes gets the first 100 plus the item as
it is now, which is what the conflict dialog needs. It is not a history view (use the events endpoint).
