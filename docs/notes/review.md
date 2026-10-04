# Independent review: Baton, `phase-4-done..HEAD`

Reviewer: independent session, read-only. Scope: `git diff phase-4-done..HEAD -- api/ web/src` against `CLAUDE.md` and `docs/SPEC.md`, plus the code those changes call.

**Scope and limits**

- Line numbers are for the committed tree (HEAD `57662ad`). For files the working tree has already changed (`api/app/db/tx.py`, `api/app/repo/notifications.py`, `web/src/features/item/ItemDetail.tsx`, `web/src/lib/useLiveUpdates.ts`) I took the HEAD version.
- **Not reviewed:** the uncommitted SSE work in the working tree (`routers/stream.py`, `realtime/*`, edits to `tx.py`, `notifications.py`, `Shell.tsx`, `ItemDetail.tsx`, `useLiveUpdates.ts`). Another session is still writing it.
- I ran **no test suite and no API request**. What I ran: read-only SQL against `baton_large` and a probe that calls the repo's query functions and EXPLAINs what they send (see `docs/PERFORMANCE.md`). A finding says "reproduced" only where a query proved it. Everything else is from reading the code and says so.
- No High findings. Five Medium, then Low.

---

## Medium

### M1. `duplicate_suggested` events reveal that hidden items exist (I3, SPEC 5.1)

`api/app/worker/handlers/duplicates.py:25-35` calls `search_repo.similar_to` (`api/app/repo/search.py:108-129`). That query has no visibility filter, on purpose ("filtered by whoever reads it"). The handler then writes the result into the item's timeline: `data={"count": len(found), "top_score": ...}`. `GET /items/{key}/events` returns `data` to everyone who can see the new item. `item_similar` is the only place the filtering is deferred to, and nothing reads that table, so no reader ever filters.

**Scenario.** A Support user (not in Compliance) raises a request to the Compliance team. Compliance items are confidential by default, so the user cannot see any other Compliance item. They title it "Q3 sanctions screening exception for Acme". If a confidential Compliance item with a similar title exists, the worker adds `duplicate_suggested {count: 1, top_score: 0.8}` to the new item. If none exists, no event. The requester can repeat this with guessed titles and learn which confidential titles exist, without ever getting a 404/403 signal. The UI only prints "found a possible duplicate" (`web/src/lib/eventText.ts:108`), but the JSON carries the numbers.

**Fix.** Count only items the item's *requester* could see (apply `visibility_clause` for the requester's context in `similar_to`), or drop `count` and `top_score` from the event. Add a T-VIS case: a viewer of an item must not see a `duplicate_suggested` whose count depends on hidden items.

### M2. No Content-Security-Policy, and Markdown loads remote images (SPEC 5.4)

SPEC 5.4 says "strict Content-Security-Policy from nginx". `web/nginx.conf:35-49` sets only `Cache-Control`; a repo-wide search finds no CSP anywhere. `docs/BUILD_PLAN.md:629` defers it to a "security pass" that has not happened, and `KNOWN_LIMITATIONS.md` does not list it.

I12 itself holds: no `rehype-raw`, no `dangerouslySetInnerHTML`. But `web/src/components/ui/Markdown.tsx:8` renders `![x](https://attacker.example/p.png)` as a real `<img>`. **Scenario:** anyone who can comment posts that image. Every viewer's browser requests it when the comment scrolls into view: a read receipt with IP and timing, from an internal tool. With a CSP (`img-src 'self' data:`) the request would not happen.

**Fix.** Add `Content-Security-Policy` in nginx (`default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'`, adjusted for what Vite emits). Also pass `components={{ img: () => null }}` (or render a link) in `Markdown.tsx`. Record the decision.

### M3. Queue pagination is not index-ordered; admin queue is a sequential scan (SPEC 13, 3.2)

`api/app/repo/items.py:578-583` sorts by `(priority, COALESCE(due_at,'infinity'), id)`. The only queue index is `work_items_team_queue_idx (team_id, status, priority, due_at, id)` (SPEC 3.2). It cannot give that order: `status` sits between `team_id` and `priority`, the sort uses a `COALESCE` expression the index lacks, and a user in several teams filters with `team_id IN (...)` anyway.

**Reproduced on `baton_large` (50,000 items):**

- Admin, first page, no filter: `Parallel Seq Scan on work_items`, 9,716 buffers, 53 ms execution. SPEC 13's last row ("No request-path query on `work_items` or `item_events` — sequential scan") is violated.
- Admin, deep page (~row 25,000): still a Seq Scan, now 102 ms. Deeper pages cost *more*, because the row-comparison filter keeps half the table before the sort.
- Admin, `assignee=me` with no status filter: Seq Scan, 25 ms. `work_items_assignee_open_idx` is partial (`WHERE status NOT IN ('resolved','closed')`), so a query without that predicate cannot use it.
- Member of 3 teams, first page: `Bitmap Heap Scan` of all 6,356 visible rows (4,029 heap blocks), then top-N sort, 26 ms. The cost tracks the size of the visible set, not the page size.

So the keyset cursor removes `OFFSET` semantics but not the cost: every page reads and sorts everything the actor can see. ENGINEERING_DECISIONS 32 already lists the admin case as "still open for phase 12" (and decision 10 as the fix); it does not record that the member path is a heap scan too, or that deep pages are slower than the first. My admin numbers (47-76 ms) are higher than the "about 20 ms" there; the host was also running another Docker session and EXPLAIN adds instrumentation cost, so compare shapes, not milliseconds.

**Fix.** Partial indexes that match the sort exactly, e.g. `(team_id, priority, (COALESCE(due_at,'infinity')), id) WHERE status NOT IN ('resolved','closed')` for team queues, and a global one without `team_id` for admins. For members of a few teams, fetch top-N per team in a `LATERAL` and merge. Update SPEC 3.2's index table, which lists an index that cannot serve its stated query. Not tested; I did not create indexes.

### M4. Search with a common word is a sequential scan for admin (SPEC 13, 8)

`api/app/repo/search.py:48-57` ORs the full-text match with the trigram match, then `rank_columns` (`search.py:60-64`) computes `ts_rank + word_similarity` for every match before the top-50 limit (`items.py:716-735`).

**Reproduced.** Admin, `q=refund` (12% of titles): `Seq Scan on work_items`, 10,706 rows ranked, **783 ms execution** (SPEC target: p95 < 250 ms); wall p95 2.85 s. Admin `settlment` (typo): 371 ms. Members stay at about 50 ms because visibility narrows the candidate set first.

**Fix.** Rank only a bounded candidate set: take the first ~200 rows from the GIN full-text match, and run the trigram branch only when that returns fewer than 50. Cost grows linearly with matches; at 10× the common-word query would be several seconds (extrapolated, not measured).

### M5. The documented typo example "refnd" finds nothing (SPEC 8, BUILD_PLAN 369)

`api/app/repo/search.py:11` promises "so a typo ("refnd") still finds "refund"", and `docs/BUILD_PLAN.md:369` and `docs/DESIGN.md:198` use the same example. The match is `'refnd' <% title` (`search.py:52`), which holds only when `word_similarity >= 0.6` (default threshold).

**Reproduced on `baton_large`:** `word_similarity('refnd','Refund not received for order 59133') = 0.5`; `select count(*) from work_items where 'refnd' <% title` returns **0**, while `'settlment' <% title` returns 4,836. The query also pays for the miss: the GIN trigram index returns 5,992 candidates, the heap recheck rejects all of them (3,583 pages read, 62-79 ms) for zero rows.

The test (`api/tests/integration/test_search.py:49`) uses `reconcilation`, a longer word with higher similarity, so it passes. `KNOWN_LIMITATIONS.md:166` mentions the threshold for "a 3-letter typo", but not that the headline example fails.

**Fix.** `SET LOCAL pg_trgm.word_similarity_threshold = 0.4` inside the search statement's transaction (or use a different operator), and add a test with `refnd`. Check the index recheck cost again afterwards, since a lower threshold returns more candidates.

---

## Low

### L1. Rapid priority edits conflict with themselves (SPEC 6.5, I13)

`web/src/features/item/useSaveFields.ts:44-46` reads the cached `version` when `save()` is *called* and passes it as `If-Match` (`:27-35`, via `input.version`). It does not use the version "returned by the previous one", which `useCommand` does for `needsIfMatch` commands (`web/src/lib/useCommand.ts:79-84`). The priority menu is not disabled while saving (`web/src/features/item/Properties.tsx:200-202`).

**Scenario (from reading, not reproduced).** The user picks P1, and before the round trip finishes picks P0. Both calls capture version N. The scope runs the second after the first, so it sends `If-Match: N` while the server is at N+1. The server returns 412 with the user's own `priority_changed` in `changes_since`. `planRebase` sees an overlap on `priority` and shows "<own name> changed the priority a moment ago. Yours wasn't saved." This is the self-conflict SPEC 6.5 says the queue prevents.

**Fix.** Resolve the version inside `mutationFn` (run time) for the first send and use the rebase version only for resends, or disable the picker while pending.

### L2. Equal `(version, last_event_id)` lets a late, older GET regress derived fields (SPEC 6.6)

`web/src/lib/itemCache.ts:26-29` lets the incoming item win a tie. `watching` and `unread_since_event_id` change without moving either number. A poll GET (`web/src/api/queries.ts:118-126`) that started before a watch toggle (`Properties.tsx:114-125`) or before `POST /read` and resolves after it overwrites the cache with the old value. The toggle flips back for up to one poll (10 s). Self-heals; cosmetic.

**Fix.** Break ties on `updated_at`, or apply the response of the newest *request*, not just the newest version.

### L3. "Mark read" can mark events the user never saw

`web/src/features/item/ItemDetail.tsx:158-170` (HEAD) posts `/read` with no event id as soon as an item opens. `api/app/repo/collab.py:36-58` stamps the item's *current* `last_event_id`. If a comment landed after the client's cached copy (items are prefetched with a 15 s stale time, `queue/prefetch.ts`), that comment is marked read before it is displayed. The unread divider never shows it, and "Your requests" (`repo/attention.py:112-122`) drops it. The docstring at `collab.py:37-41` claims the marker "cannot point past an event the actor was never shown"; it only guarantees the read is consistent with the write snapshot. SPEC 11 says "the item's current `last_event_id`", so the code follows the SPEC and the SPEC is the weak part.

**Fix.** Send the `last_event_id` the screen displayed and store `LEAST(sent, current)`.

### L4. Out-of-range cursors and ids probably return 500, not 400 (SPEC 12)

From reading, not run. `api/app/repo/items.py:624` builds `sa.literal(int(priority), sa.SmallInteger())` from the client's cursor, and the `except` at `:646` does not catch the asyncpg `DataError` raised when it is executed. A cursor with priority 99999 would reach the database as an int2. Likewise `cursor` is `ge=1` with no upper bound in `routers/me.py:91`, `routers/overview.py:56` and `routers/admin.py:55`, and `after_event_id` in `routers/items.py:365`; a value above 2^63-1 fails to encode. `db/errors.py:4-6` says an unmodelled database error "stays a 500 on purpose", so this is a bad-input 500.

**Fix.** Validate the cursor's priority range, and add `le=2**63-1` to integer cursors.

### L5. Dashboard "five oldest" is not "one index probe"

`api/app/repo/stats.py:108-118` says the `LATERAL` costs "one index probe and a top-5 sort". There is no `(team_id, created_at)` index, so each team's subquery reads all its open items. **Reproduced (admin):** 24,933 buffers, 453 rows per team on average, 178 ms for 40 teams, and 278 ms for the whole dashboard (`docs/PERFORMANCE.md`). It polls every 30 s while open (`web/src/api/queries.ts:50`).

**Fix.** `CREATE INDEX ... ON work_items (team_id, created_at, id) WHERE status NOT IN ('resolved','closed')`.

### L6. `sla_breached_at` is never cleared, so a rescheduled item stays "Overdue"

`api/app/repo/items.py:470-494` sets it once (as SPEC 9 says). If a lead later moves `due_at` a week out, or lowering priority recomputes an auto due date into the future (SPEC 4.2), the item keeps `sla_breached_at`. `workflow.py:961-963` then shows "Overdue" (severity high) and `repo/attention.py:88` keeps it in "Yours, overdue or urgent", while the properties panel (`Properties.tsx:48-56`) and the `overdue` filter say it is not overdue. Two definitions of "overdue" disagree. Decide whether a due-date change resets the breach, and write it into the SPEC.

### L7. Docs and stack disagree on live updates

> **Rejected (one line): obsolete, SSE was merged in `c693cc2` after this review; CLAUDE.md now matches the code.**

`CLAUDE.md` (stack and the LISTEN/NOTIFY hazard) says SSE. HEAD polls every 10 s; SPEC 10 and ENGINEERING_DECISIONS 50 say so. Every event still runs `pg_notify` (`db/tx.py:158-241`) with nothing listening, and `web/nginx.conf:26-34` proxies `/api/v1/stream` to a route that does not exist at HEAD. Harmless, but update `CLAUDE.md` or finish SSE; a reviewer reading the stack table will assume SSE.

---

## Checked, no finding

- **I3, every query that returns or counts items.** `visibility_clause` is in: `list_items`, `search_items`, `facet_counts`, `first_items`, `count_up_to` (all five attention sections), `similar_items`, `get_visible_by_key`, `visible_item_id`, `collab.mark_read`, notifications list, decision log, all four dashboard statements, and the `duplicate_of` join. Deliberately unfiltered and reasonable: `lock_by_key` (callers pre-check), `get_by_key_unchecked` (post-transfer answer), `lock_keys_owned_by` (leads/admins only), `mark_sla_breached` and `similar_to` (system; see M1).
- **Hidden vs missing.** `lock_visible` checks visibility *before* taking the row lock (`services/items.py:225-236`), so a user who cannot see an item gets 404, not a 503 after waiting on someone else's lock.
- **Idempotency on new POSTs.** Comments require a key and run through `run_idempotent`. `notifications/read` and `admin/jobs/{id}/retry` accept one. `/read` and `PUT/DELETE /watch` have none, as the SPEC table says; they are naturally idempotent (`GREATEST`, set membership), so I6's "every state-changing POST" is met in effect, not by the layer.
- **Notifications.** The fan-out re-checks `can_view` against the item as it is now and a freshly loaded actor context (`worker/handlers/notify.py`); reads filter by visibility at read time.
- **Outbox and scheduler.** `SKIP LOCKED` claim, lease, `complete`/`fail` guarded by `status='pending'`; the SLA sweep takes `pg_try_advisory_xact_lock` and runs only in the worker. The sweep never waits for a lock (`SKIP LOCKED`), so it cannot form a deadlock cycle with the id-ordered locking in `lock_keys_owned_by`.
- **I9.** No comment edit/delete route exists. **I12.** No `rehype-raw`, `dangerouslySetInnerHTML` or `innerHTML` in `web/src`. **I14.** No raw hex outside `tokens.css`.
- **I13.** Item commands share `scope: item:<id>` (including watch and read, which go through `useCommand`); the detail cache merges by `(version, last_event_id)`. Lists are not merged, but every command success and every poll invalidates them, which cancels an older in-flight fetch, so I could not build a stale-overwrite scenario for lists.

---

## Triage (Kumar's session, 2026-10-04, tree `552e526`)

The review has no Critical or High finding, so nothing was fixed in code and no test was added. Per the brief, Medium and Low go to `docs/KNOWN_LIMITATIONS.md` ("Findings from the independent review").

| # | Verdict | How checked |
|---|---|---|
| M1 | **Confirmed**, kept Medium (leaks that a similar item exists, not its content) | Read: `handlers/duplicates.py` calls `similar_to`, which has no visibility filter, and writes `count`/`top_score` into an event the requester can read. Not run. First candidate to fix if time allows |
| M2 | **Confirmed** | `grep` finds no CSP in `web/nginx.conf` or `web/src`; `Markdown.tsx` has no `img` override |
| M3 | Accepted as written | Not re-run. Matches decision 32's admin note |
| M4 | Accepted as written | Not re-run |
| M5 | **Confirmed, reproduced** | `word_similarity('refnd','Refund not received for order 59133') = 0.5` and `count(*) ... 'refnd' <% title = 0` on `baton_large` |
| L1-L3, L5, L6 | Accepted as written | Read only, not re-run |
| L4 | Accepted (reading only) | The cursor `except` at `repo/items.py:646` does not cover `DataError`; not run |
| L7 | **Rejected: obsolete** | Written against `57662ad`; SSE merged since (`c693cc2`, `routers/stream.py` exists), so CLAUDE.md and the code agree |
