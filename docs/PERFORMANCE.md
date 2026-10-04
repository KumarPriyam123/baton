# Performance

Measured by an independent reviewer, read-only, against the large seed (`baton_large`). This document says what was measured, what the plans are, what I expect to break at 10×, and what was **not** measured.

**Status of these numbers.** Measured by the reviewer on the tree at `57662ad` (before SSE merged in `c693cc2`); nothing was re-run for the current tree except one check, that `word_similarity('refnd', ...)` is 0.5 and `'refnd' <% title` matches 0 rows on `baton_large` (confirms the `refnd` row). Only database and query-function time was measured: no HTTP layer, no commands, no worker, no load test, no real 10x data. Items marked "extrapolated" are reasoning, not measurements. Where a wall-clock p95 exceeds a SPEC 13 target, the table below says so; the reviewer's host was busy, so those p95s are noisy.

**Headline.** Member and lead *database execution* is 2-80 ms and mostly uses indexes (wall p95 reached 100-305 ms on a loaded host, and the attention endpoint missed its target there), except that the queue and search read and rank *everything the user can see*, not one page. For an **admin**, who sees all 50,000 items, the queue is a sequential scan, search on a common word takes **0.78 s**, and the dashboard takes **0.28 s**. SPEC 13's "no sequential scan on `work_items` in a request path" is not met for admins. The documented typo example (`refnd`) returns nothing (see review M5).

## Dataset and environment

| | |
|---|---|
| Database | `baton_large` on PostgreSQL 16.14 (Docker Desktop on Windows, WSL2). Created by the large seed; I did not seed or modify it. |
| Rows | 50,000 items (18,107 open; 11,295 confidential), 586,983 events, 127,714 comments, 17,494 approvals, 198,891 notifications, 2,000 users, 40 teams, 2,423 memberships. Outbox is empty (the seed bypasses it). |
| Size | 462 MB database. `work_items` 136 MB (76 MB heap; trigram index 20 MB, full-text index 14 MB), `item_events` 211 MB. |
| Settings | defaults: `shared_buffers` 128 MB, `work_mem` 4 MB, 2 parallel workers per gather, `random_page_cost` 4. Statistics are fresh (analyzed 2026-10-03). |
| Actors | **member**: `fatima.chopra1152` (member of PAY, PRT, SDK; 6,356 visible rows without filters). **lead**: `priya.lead` (PAY lead). **admin**: `admin@baton.test`. |

## Method, and how far to trust the numbers

A script run inside the `api` container called Baton's own query functions (`list_items`, `search_items`, `attention`, `team_stats`, `facet_counts`, `get_visible_by_key`, the decision log, notifications) with the real actor context for each persona. It captured every statement they send, rendered it with literal values, and ran `EXPLAIN (ANALYZE, BUFFERS)` three times, keeping the fastest. Each function was also timed end to end 15 times after one warm-up. Every transaction was `SET TRANSACTION READ ONLY` and rolled back.

- **exec / plan** = Postgres execution and planning time from EXPLAIN, summed over the statements a call sends. `EXPLAIN ANALYZE` adds instrumentation cost: for row-heavy plans it reads high (the `refnd` row below: 62 ms exec vs 25 ms wall).
- **wall p50 / p95** = time in the Python process including the round trips to the `db` container and row hydration, 15 runs. This host was also running another Docker session, so p95 is noisy. A point read that takes 0.2 ms in the database showed 15-67 ms wall (lead 67 ms). Treat wall numbers as orders of magnitude, and rely on EXPLAIN for plan shapes.
- Results are warm-cache. The first admin page read 1,469 buffers from disk, the rest from cache.
- **Not the HTTP layer:** no nginx, auth, session lookup, JSON serialisation or the Pydantic models. API p95 will be higher than the wall column.
- ENGINEERING_DECISIONS 32 recorded 3-12 ms for member/lead lists and "about 20 ms" for an admin. My figures are higher (member 18-28 ms exec, admin 47-76 ms). I used a member with a large visible set, EXPLAIN adds overhead, and the host was busy. The plan shapes agree with decision 32.

## Results

exec/plan = EXPLAIN ms (best of 3, all statements summed). wall = p50 / p95 ms over 15 runs. **SEQ** = sequential scan of `work_items`; otherwise indexes or bitmap scans only.

### Queue (`GET /items`, limit 50)

| Query | member exec/plan | member wall | lead exec/plan | lead wall | admin exec/plan | admin wall | admin scan |
|---|---|---|---|---|---|---|---|
| first page, sort=priority | 26 / 5 | 52 / 75 | 28 / 9 | 51 / 71 | 53 / 5 | 92 / 475 | SEQ |
| first page, sort=due | 24 / 4 | 65 / 108 | 35 / 6 | 96 / 155 | 47 / 4 | 81 / 111 | SEQ |
| first page, sort=updated | 18 / 4 | 37 / 91 | 21 / 6 | 54 / 201 | 51 / 5 | 169 / 215 | SEQ |
| first page, sort=created | 18 / 5 | 34 / 46 | 20 / 5 | 39 / 57 | 76 / 8 | 101 / 202 | SEQ |
| team=PAY, open statuses, sort=priority | 9 / 4 | 23 / 33 | 13 / 6 | 45 / 102 | 14 / 9 | 37 / 63 | index |
| assignee=me, sort=priority | 11 / 4 | 26 / 37 | 11 / 4 | 36 / 60 | 26 / 7 | 48 / 89 | SEQ |
| deep page (~row 1,500), priority | 18 / 4 | 31 / 49 | 14 / 4 | 34 / 166 | n/a | n/a | |
| deep page (~row 1,500), updated | 12 / 3 | 26 / 44 | 34 / 8 | 45 / 118 | n/a | n/a | |
| deep page, team=PAY open, priority | 8 / 5 | 24 / 44 | 18 / 7 | 64 / 77 | n/a | n/a | |
| deep page (~row 25,000), priority | n/a | n/a | n/a | n/a | 102 / 11 | 103 / 346 | SEQ |
| deep page (~row 25,000), updated | n/a | n/a | n/a | n/a | 95 / 6 | 155 / 268 | SEQ |

**Plans.**

- *Admin, unfiltered:* `Gather Merge` over `Parallel Seq Scan on work_items` (9,716 buffers in the scan), top-N heapsort, then the 51-row joins by primary key. The page is cut before the joins (decision 32 works), but the cut needs all 50,000 rows.
- *Admin deep page:* same Seq Scan with a `ROW(priority, COALESCE(due_at,'infinity'), id) > ROW(...)` filter that removes 25,001 rows. It is slower than page 1 because more rows reach the sort. The keyset cursor does not make deep pages cheap.
- *Member, unfiltered:* `Bitmap Heap Scan` on `work_items` via `work_items_requester_idx` OR `work_items_team_updated_idx`: 6,356 rows, 4,029 heap blocks, then top-N sort. Index-assisted, but it reads every visible row.
- *Member/admin with `team=PAY` and statuses:* `work_items_team_queue_idx` (bitmap), ~1,500 rows, then the same sort. This is the shape the queue index was built for, and it still cannot supply the order (see "What I'd change").
- Both the member and admin cases join the 51 page rows to `teams`, `users`, `item_events` (PK), `item_reads`, `approvals` (LATERAL on `approvals_item_idx`), and `watchers` (PK) with index scans; none of those scale with table size.

### Search (`GET /items?q=`, top 50)

| Query | member exec/plan | member wall | lead exec/plan | lead wall | admin exec/plan | admin wall | admin scan |
|---|---|---|---|---|---|---|---|
| key `PAY-1234` | 4 / 5 | 26 / 132 | 9 / 31 | 91 / 129 | 10 / 12 | 53 / 92 | index |
| word, common (`refund`, 12% of items) | 50 / 11 | 99 / 143 | 53 / 7 | 85 / 197 | **783** / 7 | **437 / 2,851** | SEQ |
| word, mid (`kyc-check`, 0.5%) | 8 / 6 | 22 / 26 | 11 / 8 | 37 / 51 | 62 / 16 | 104 / 248 | index |
| typo `refnd` (**returns 0 rows**) | 62 / 6 | 25 / 33 | 51 / 5 | 28 / 33 | 79 / 8 | 167 / 409 | index |
| typo `settlment` | 82 / 11 | 58 / 115 | 43 / 7 | 53 / 81 | 371 / 13 | 528 / 1,437 | index |
| two words `merchant payout` | 35 / 9 | 53 / 118 | 43 / 21 | 46 / 64 | 242 / 32 | 192 / 363 | index |

**Plans.**

- *Key:* `work_items_key_key` index scan, as designed.
- *Admin, common word:* `Seq Scan on work_items`, filter `search @@ 'refund' OR 'refund' <% title`, 10,706 of 50,000 rows pass; each gets `ts_rank + word_similarity` computed, then top-N. The planner picks a scan because the OR matches 21% of the table. 754 ms of the 783 is the scan and rank.
- *Member, common word:* `BitmapOr` of `work_items_search_idx` and `work_items_title_trgm_idx`, ANDed with the visibility bitmap; 2,060 rows ranked; 50 ms.
- *`refnd`:* `work_items_title_trgm_idx` returns 5,992 candidates; the heap recheck rejects all of them (`Rows Removed by Index Recheck: 5992`, 3,583 heap blocks read). The `word_similarity('refnd','Refund…')` is 0.5, below the 0.6 default threshold. Zero results at the cost of 60-80 ms.

### Attention and dashboard

| Query | member exec/plan | member wall | lead exec/plan | lead wall | admin exec/plan | admin wall | admin scan |
|---|---|---|---|---|---|---|---|
| attention (5 sections; 9 statements for member/lead, 5 for admin) | 10 / **56** | 137 / 281 | 19 / 26 | 143 / 305 | 2 / 21 | 91 / 327 | index |
| dashboard (4 statements) | 38 / 2 | 106 / 183 | 28 / 2 | 113 / 278 | **278** / 2 | **372 / 1,023** | SEQ |

**Plans.**

- *Attention:* every section uses its intended index (`work_items_assignee_open_idx`, `work_items_needs_owner_idx`, `work_items_going_stale_idx`, `approvals_one_pending`, `work_items_requester_idx`, `item_reads_pkey`). Execution is 2-19 ms in total; the cost is **planning**: the item-view query plans for 4-18 ms per statement, so planning (56 ms) is several times the execution (10 ms) for a member. Each section is also a separate round trip.
- *Dashboard, member/lead:* index and bitmap scans over the actor's visible rows (`work_items_requester_idx`, `work_items_team_updated_idx`, `work_items_assignee_open_idx`); 28-38 ms.
- *Dashboard, admin:*
  - counts (`GROUP BY team_id` with `FILTER`): Seq Scan on 18,107 open rows, 51 ms;
  - **"five oldest" LATERAL: 178 ms**, 24,933 buffers. There is no `(team_id, created_at)` index, so each of the 40 teams reads all its open items (453 on average), sorts, and keeps 5;
  - load per owner: Seq Scan, 48 ms.

### Other reads

| Query | member exec/plan | member wall | lead exec/plan | lead wall | admin exec/plan | admin wall |
|---|---|---|---|---|---|---|
| `GET /items/{key}` | 0 / 10 | 20 / 27 | 0 / 20 | 67 / 105 | 0 / 5 | 15 / 30 |
| facets, no filter | 19 / 1 | 27 / 47 | 13 / 0 | 18 / 24 | 74 / 1 (SEQ) | 98 / 185 |
| facets, team=PAY | 19 / 1 | 22 / 52 | 38 / 1 | 19 / 62 | 15 / 0 | 19 / 25 |
| decision log, no filter | 5 / 2 | 12 / 15 | 9 / 10 | 31 / 46 | 1 / 1 | 6 / 10 |
| decision log, team=PAY | 3 / 1 | 8 / 9 | 2 / 1 | 16 / 30 | 2 / 1 | 7 / 13 |
| notifications, unread (21) | 0 / 1 | 4 / 7 | 2 / 1 | 8 / 16 | 0 / 1 | 4 / 5 |

`GET /items/{key}` and the notification and decision queries are index-only paths and are not a concern. Planning time (5-20 ms) is larger than execution for the point read.

## Against SPEC 13

| Target | Result (database + function, no HTTP layer) |
|---|---|
| `GET /items` first page p95 < 150 ms | Member and lead: met in the wall column for every sort except lead/due (155) and lead/updated (201) — noisy. Admin: SEQ, 47-76 ms exec, wall p95 111-475. Borderline. |
| `GET /items/{key}` p95 < 100 ms | Met (27-30 ms; lead 105 was host noise on a 0.4 ms query). |
| Search p95 < 250 ms | Member/lead: met. **Admin: not met** for common words (2.85 s), `settlment` (1.4 s), `refnd` (409 ms, no results) and two words (363 ms). |
| `GET /me/attention` p95 < 200 ms | Wall p95 281-327 ms, **not met in this environment**, but 2-19 ms of that is execution. It is planning and 5-9 round trips on a loaded host. Unproven either way without the HTTP path on a quiet machine. |
| Commands p95 < 150 ms | **Not measured.** |
| No request-path sequential scan on `work_items`/`item_events` | **Not met for admins:** unfiltered queue (all four sorts), `assignee=me` without a status filter, facets without a filter, dashboard counts, per-owner load, and common-word search. Met for members and leads. |

## What breaks first at 10× (extrapolated, not measured)

I did not seed 500,000 items. These are estimates from the plans above, which are linear in the rows scanned.

1. **Search on a common word, for anyone whose visible set is large.** 783 ms → several seconds (10× the 10,706 ranked rows). It is already over target at 1×.
2. **The admin dashboard.** 278 ms → ~2-3 s, refetched every 30 s by every open dashboard tab (`useTeamStats`, `refetchInterval: 30_000`). Both the counts and the per-team "oldest 5" scale with open items.
3. **Queue and facets for admins and for members of large teams.** 53 ms → ~0.5 s (Seq Scan of 760 MB of heap, parallel and cached). If the growth comes from *more teams*, members are unaffected. If it comes from *bigger teams*, a PAY member's 6.3k visible rows become 63k, and so does the sort input of every page.
4. **Client multiplier** (written for the polling client at `57662ad`; the SSE client coalesces invalidations, and this has not been re-measured). `useInfiniteQuery` refetches every loaded page, sequentially, on each 10 s poll (`web/src/lib/useLiveUpdates.ts`, `refetchType: "active"`) and after every command. A user who has scrolled 1,000 rows triggers 20 list queries per refetch, each costing the full visible-set sort. From reading TanStack Query semantics; not measured.
5. **Planning cost** of the 12-join item view (4-18 ms per statement, unchanged by data size). It dominates attention (9 statements) and would dominate at any scale where the queries are cheap.
6. **Unbounded tables.** One outbox row per event and no cleanup (SPEC 9: not built): 587k events means 587k `done` outbox rows once the system has run that long; `idempotency_keys` and expired `sessions` also grow. The pending index keeps the claim fast, but the table and its autovacuum cost grow.

The point reads, `item_events` by item, notifications, decision log, and the attention indexes are not at risk at 10×.

## What I'd change, in order

1. **Search: bound the work.** Rank only a candidate set (the first ~200 full-text matches); run the trigram branch only when full text returns fewer than 50. Set `pg_trgm.word_similarity_threshold` to ~0.4 for the statement so `refnd` finds `refund` (and re-measure recheck cost). Expected: the admin common-word query drops from ~780 ms to tens of ms. Untested.
2. **Queue: indexes that match the sort.** Partial indexes on `(team_id, priority, COALESCE(due_at,'infinity'), id) WHERE status NOT IN ('resolved','closed')` for team queues, plus a global one for admins, and for a user in a few teams a per-team `LATERAL` top-N merged in SQL, so a page reads ~50 rows instead of everything visible. This is what decision 10 anticipated. Also fix SPEC 3.2's index table, which lists an index that cannot serve the stated sort. Untested; I did not create any index.
3. **Dashboard.** Add `(team_id, created_at, id) WHERE status NOT IN ('resolved','closed')` for the "oldest 5". Cache the admin figures server-side for 30 s (SPEC 15 already puts materialised counters out of scope; a short cache is smaller than that).
4. **Attention.** Cut round trips and planning: select the 10 ids per section with a light query, then load the item view for the union of ids in one statement; skip a section's count query when the page is not full (already done) and run the five counts in one statement.
5. **Client.** Poll the first page only, and invalidate the rest lazily; or cap `maxPages` on the queue's infinite query.
6. **Retention.** Build the cleanup job SPEC 9 describes (outbox `done` rows, idempotency keys, sessions) before any long-running deployment.

## Not measured

- The HTTP path: nginx, session and CSRF checks, serialisation, `allowed_actions`/`next_step` computation per row, and the effect of a connection pool under concurrency. No load test, no throughput number.
- **Writes and commands** (claim, PATCH, transitions, comments) and their SPEC 13 p95 < 150 ms target. No contention numbers either, and `scripts/bench.py` was not run.
- The worker (outbox claim, notify fan-out, SLA sweep) at volume. The notify handler loads one actor context per recipient (`worker/handlers/notify.py:28-31`); I did not test a watcher-heavy item.
- Cold-cache behaviour, a quiet host, a real 10× dataset, or index changes. Everything under "10×" and "What I'd change" is reasoning from the plans, not a measurement.
- Frontend: first load, bundle size, scroll smoothness over 1,000 rows (virtualised in `QueuePage.tsx`, not exercised), SSE (merged after these measurements).
- The decision log's worst case: a member with few visible decisions who scans a long `is_decision` range to fill a page. The measured personas had plenty.
