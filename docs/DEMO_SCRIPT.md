# Demo script (5 minutes)

Seven moments, each one a critical behaviour from SPEC section 14. Every step below was run against the
real UI with two signed-in browsers (separate cookies); the wording is what the screen says. Everything runs on the demo seed;
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
2. In window A press `c` (wait until the inbox has loaded), choose team **Payments**, type **Engineering**,
   title `Demo: race`, **Create request**. A toast says "Created PAY-nnn". Window B finds it in **Queue**
   (`g q`) or opens `/items/PAY-nnn`. Open the same item in both windows.
3. Count down, click **Assign to me** in both windows at the same moment.

**Expect:** the winner sees "Assigned to you" and the status `In progress`. The other window shows a calm
toast, "Asha Rao took PAY-nnn 0 seconds ago.", and its pane already reads "In progress with Asha", with
the track updated and the buttons replaced by "You can comment on this request, but there's nothing else
for you to do on it." That second window never reloaded: the change reached it over SSE (live updates).
**Say:** the claim is one conditional `UPDATE ... WHERE assignee_id IS NULL AND status = 'new'`; the
database picks the winner, not the application. **Proof:** `test_claim_twenty_concurrent_requests_exactly_one_wins`.

## 2. Stale edit conflict (CB2, CB8, 60 s)

1. Window A: Asha, who owns the item from step 1. Open it, **Edit** the description, type
   `Checked the settlement batch.`, do **not** save yet.
2. Window B: sign in as **Priya** (`priya.lead@baton.test`) and open the same item. (Rahul cannot do this
   step: only the requester, the owner or a lead can edit the description, so he has no Edit button.)
   **Edit** the description, type something else, **Save description**.
3. Window A: **Save description**.

**Expect:** window A gets "This request changed while you were editing", "Priya Nair changed the
description", a diff ("Saved now → your draft"), and two choices: **Discard my draft** or **Save my
version**. Nothing was silently overwritten. Reload window A: the draft is still in the editor (it is kept
locally) under the banner "The description changed while you were editing. Your draft is kept." with
**Review changes** and **Keep editing**.
**Say:** every edit carries `If-Match: <version>`; the server answers 412 when the version moved, and the
client either rebases (other fields changed) or asks (same field). **Proof:** `test_items_concurrency.py`
(T-STALE), `Description.test.tsx`, Playwright `detail-concurrency.spec.ts`.

## 3. Approval flow and invalidation (CB4, CB5, 90 s)

1. Window A: sign in as **Meera** (`meera@baton.test`), **New request**, type *Payment investigation*,
   title `Demo: refund stuck`. Payment investigations require approval.
2. Window B: sign in as **Asha**, open it, **Assign to me**, then **Resolve**, write anything under "What
   was done?" and confirm. A toast refuses: "This needs an approval before it can be resolved. Use Request
   approval." (422 `APPROVAL_REQUIRED`).
3. Asha: **More → Request approval** (the note is optional). Window A: sign in as **Priya**
   (`priya.lead@baton.test`, Payments lead); her item shows "Needs your approval" with **Approve** and **Reject**.
4. Before Priya approves, Asha edits the **description** and saves. Priya's screen changes by itself: "Asha
   withdrew the approval", "Approval withdrawn: the description changed. Request approval again before
   resolving.", and **Approve** is gone.
5. Asha requests approval again; Priya clicks **Approve**. Asha can now **Resolve**.

Also try: Asha has no **Approve** button because she is not a lead (the four-eyes rule for a lead who raised the
request, and the admin rule, are proven by the policy matrix, not shown in this UI path).
**Say:** an approval is bound to a hash of the content; changing title, description, type or team
invalidates it in the same transaction. **Proof:** T-FLOW (720 cells), T-APPROVE-RACE (50 rounds of approve racing an edit).

## 4. Idempotent double-submit (CB3, 45 s)

1. As anyone: **New request**, fill the title (do not submit yet).
2. Browser devtools, Network: set throttling to **Slow 3G**, then double-click **Create request**.
   Only one request leaves the browser (the button is disabled while it is pending): one item, `Created PAY-nnn`.
3. The server side of the same promise, by hand (sign in with a cookie jar as in HANDOFF.md, "Trying the items
   API by hand"): send the same `Idempotency-Key` twice. The second answer is the first item again
   (same `key`) with the header `idempotent-replayed: true`.
4. The harsh case (the first answer is lost after the server committed; the client retries with the same
   key): `cd web && BASE_URL=http://localhost:8080 npx playwright test create-double-submit` (passes: one item).

**Expect:** exactly one item in every variant.
**Say:** the browser makes one key per user action and reuses it on every retry; the key is stored in the same
transaction as the item, so "committed but the answer got lost" replays the first answer. **Proof:**
`test_ten_concurrent_creates_with_one_key_make_exactly_one_item_and_ten_equal_answers`, `create-double-submit.spec.ts`.

## 5. Authorization: a confidential item is a 404 (CB6, 45 s)

1. Sign in as **Farah** (`farah@baton.test`, Compliance member). Open `/items/CMP-17`: after a moment the
   pane says "CMP-17 isn't available. It moved to another team, or you no longer have access to it." with
   **Back to the queue**. The same item does not appear in her queue, search (`q=CMP-17` finds nothing),
   counts or inbox.
2. Sign in as **Ishaan** (`ishaan.lead@baton.test`, Compliance lead): `/items/CMP-17` opens.
3. As **dev.viewer** (`dev.viewer@baton.test`), open any Payments item: no action buttons at all ("You can
   comment on this request, but there's nothing else for you to do on it."); calling `claim` directly gives
   403 "Only members and leads of Payments can claim items."
   Farah's 404 body is identical to the one for a key that does not exist (`CMP-9999`).

```bash
# sign in with a cookie jar as Farah (see HANDOFF.md, "Trying the items API by hand"), then:
curl -i -b jar http://localhost:8080/api/v1/items/CMP-17      # 404 problem+json, same as a key that does not exist
```

**Say:** not visible is 404 (never confirm existence), visible but not allowed is 403. One SQL clause
(`visibility_clause()`) is in every query that returns items or counts. **Proof:** T-VIS parity test
(`test_visibility_parity.py`), the 2,475-cell policy matrix.

## 6. Worker catch-up (CB7, 45 s)

1. Stop the worker: `docker compose stop worker`.
2. As Asha, comment on an item that Rahul owns or watches (for example `PAY-10`; the requester, the
   owner and watchers are notified). The comment, the event and the item version all commit; nothing waits
   for the worker.
3. See the backlog: `docker compose exec db psql -U baton -c "select status, count(*) from outbox group by 1"`
   shows one `pending` row.
4. `docker compose start worker`. Within seconds the query shows `pending` gone (all `done`), and Rahul's
   `GET /api/v1/me/notifications` (or the bell in his rail) has "commented" on PAY-10 from Asha Rao.

**Say:** the outbox row is written in the same transaction as the change, so work cannot be lost or invented by
a rollback; delivery is at least once and every handler is idempotent (UNIQUE `(user_id, event_id)`).
Two workers split the jobs with `FOR UPDATE SKIP LOCKED` (`docker compose up --scale worker=2`).
**Proof:** `test_two_runners_against_500_jobs_do_each_side_effect_exactly_once`, lease takeover and backoff tests in `test_worker.py`.

## 7. Command palette (20 s)

Press `Ctrl+K` (or `Cmd+K`) anywhere: "Go to" (New request, Inbox, Queue, My work, My requests, Dashboard,
Decisions, team queues and settings). Type `refund`: matching requests appear under "Requests" (`PAY-5 Refund
stuck for order 80770`, ...); Enter opens one, Esc closes. As **admin**, the rail also has **Jobs**
(`/admin/jobs`, with retry for dead jobs); as a lead, **Payments settings** manages members.

---

## If asked: what is not there

Live updates are SSE with a per-process in-memory replay buffer and 10-second polling as a fallback; there is
no "N items changed" bar on the list; member endpoints take no `Idempotency-Key`; the dashboard lacks median age
and the 14-day series. See [KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md), which is ordered by importance.
