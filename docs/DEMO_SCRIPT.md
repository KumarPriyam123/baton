# Demo script (5 minutes)

Six moments, each one a critical behaviour from SPEC section 14. Everything runs on the demo seed;
passwords are all `baton-demo` (README has the account table).

**Setup (before the demo, 1 minute):**

```bash
docker compose run --rm seed python -m scripts.seed --size demo --reset   # clean data
docker compose up --build                                                  # http://localhost:8080
```

Open two browser windows that do not share cookies: window A normal, window B private (or another
browser). Zoom both so the item pane is readable. If 8080 is taken, use your `WEB_PORT`.

---

## 1. Claim race: one winner (CB1, 45 s)

1. Window A: sign in as **Asha** (`asha@baton.test`). Window B: **Rahul** (`rahul@baton.test`).
2. In both, press `c`, create a request to **PAY**, type *Engineering*, title `Demo: race`. Only one of
   you creates it; the other finds it in **Queue** (`g q`). Open the same item in both windows.
3. Count down, click **Assign to me** in both windows at the same moment.

**Expect:** one window becomes the owner, status `in progress`. The other shows a calm message that the
item was already taken, by whom and when, and its action bar updates.
**Say:** the claim is one conditional `UPDATE ... WHERE assignee_id IS NULL AND status = 'new'`; the
database picks the winner, not the application. **Proof:** `test_claim_twenty_concurrent_requests_exactly_one_wins`.

## 2. Stale edit conflict (CB2, CB8, 60 s)

1. Both windows on the item from step 1. Window A (the owner): **Edit** the description, type
   `Checked the settlement batch.`, do **not** save yet.
2. Window B: **Edit** the description, type something else, **Save description**.
3. Window A: **Save description**.

**Expect:** window A gets "This request changed while you were editing" with a diff (saved now → your
draft), and two choices: **Save my version** or **Discard my draft**. Nothing was silently overwritten;
A's draft survives a page reload (it is kept locally).
**Say:** every edit carries `If-Match: <version>`; the server answers 412 when the version moved, and the
client either rebases (other fields changed) or asks (same field). **Proof:** `test_items_concurrency.py`
(T-STALE), `Description.test.tsx`, Playwright `detail-concurrency.spec.ts`.

## 3. Approval flow and invalidation (CB4, CB5, 90 s)

1. Window A: sign in as **Meera** (`meera@baton.test`), **New request**, type *Payment investigation*,
   title `Demo: refund stuck`. Payment investigations require approval.
2. Window B: sign in as **Asha**, open it, **Assign to me**, then try **Resolve**. It is refused: this
   type needs an approved request first (422 `APPROVAL_REQUIRED`).
3. Asha: **Request approval**. Window A: sign in as **Priya** (`priya.lead@baton.test`, Payments lead);
   the item shows "Needs your approval".
4. Before Priya approves, Asha edits the **description** and saves. Priya's panel withdraws the request.
5. Asha requests approval again; Priya clicks **Approve**. Asha can now **Resolve**.

Also try: Asha cannot approve her own request (four-eyes, 403), and an admin cannot approve unless they lead the team.
**Say:** an approval is bound to a hash of the content; changing title, description, type or team
invalidates it in the same transaction. **Proof:** T-FLOW (720 cells), T-APPROVE-RACE (50 rounds of approve racing an edit).

## 4. Idempotent double-submit (CB3, 45 s)

1. As anyone: **New request**, fill the title (do not submit yet).
2. Browser devtools, Network: set throttling to **Slow 3G**, then double-click **Create request**.
   For the harsher case (the first answer is lost after the server committed), run the Playwright test
   that drops it: `cd web && BASE_URL=http://localhost:8080 npx playwright test create-double-submit`.

**Expect:** exactly one item (`Created PAY-nnn`), and the second request's response carries
`Idempotent-Replayed: true`.
**Say:** the browser makes one key per user action and reuses it on every retry; the key is stored in the same
transaction as the item, so "committed but the answer got lost" replays the first answer. **Proof:**
`test_ten_concurrent_creates_with_one_key_make_exactly_one_item_and_ten_equal_answers`, `create-double-submit.spec.ts`.

## 5. Authorization: a confidential item is a 404 (CB6, 45 s)

1. Sign in as **Farah** (`farah@baton.test`, Compliance member). Open `/items/CMP-17`: "not found".
   The same item does not appear in her queue, search, counts or inbox.
2. Sign in as **Ishaan** (`ishaan.lead@baton.test`, Compliance lead): `/items/CMP-17` opens.
3. As **dev.viewer**, open any Payments item: no claim, assign or resolve buttons; calling those endpoints directly gives 403 with a reason.

```bash
# sign in with a cookie jar as Farah (see HANDOFF.md, "Trying the items API by hand"), then:
curl -i -b jar http://localhost:8080/api/v1/items/CMP-17      # 404 problem+json, same as a key that does not exist
```

**Say:** not visible is 404 (never confirm existence), visible but not allowed is 403. One SQL clause
(`visibility_clause()`) is in every query that returns items or counts. **Proof:** T-VIS parity test
(`test_visibility_parity.py`), the 2,475-cell policy matrix.

## 6. Worker catch-up (CB7, 45 s)

1. Stop the worker: `docker compose stop worker`.
2. As Asha, comment on an item that Rahul watches (or any item: the requester and watchers are notified).
   The comment, the event and the item version all commit; nothing waits for the worker.
3. See the backlog: `docker compose exec db psql -U baton -c "select status, count(*) from outbox group by 1"` shows `pending` rows.
4. `docker compose start worker`. Run the query again: `pending` drains to `done`, and the watcher's
   `GET /api/v1/me/notifications` now has the notification.

**Say:** the outbox row is written in the same transaction as the change, so work cannot be lost or invented by
a rollback; delivery is at least once and every handler is idempotent (UNIQUE `(user_id, event_id)`).
Two workers split the jobs with `FOR UPDATE SKIP LOCKED` (`docker compose up --scale worker=2`).
**Proof:** `test_two_runners_against_500_jobs_do_each_side_effect_exactly_once`, lease takeover and backoff tests in `test_worker.py`.

---

## If asked: what is not there

Live updates are SSE (with 10-second polling as a fallback); there are no dashboard, decisions, teams or jobs screens (the
jobs endpoint exists); not every property is editable inline. See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md),
which is ordered by importance.
