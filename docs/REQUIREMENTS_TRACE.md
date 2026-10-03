# Requirements trace

Every requirement in the Newtonite brief, where Baton meets it, the phase that builds it, and the evidence that proves it. Claude Code ticks a row only when the proof exists and passes.

Status: ☐ not started · ◐ built, proof pending · ☑ proven

## The situation (pain points the product must fix)

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| S1 | Requests involve several people | single owner + requester + watchers + comments; notifications to all of them | 3, 5, 6 | timeline shows every participant; T-OUTBOX notify tests | ☐ |
| S2 | Some are urgent | priority P0–P3, SLA due dates, overdue sweep, attention sections | 3, 5, 6 | attention tests; SLA sweep test | ☐ |
| S3 | Some require approval before they can move forward | approval gate on resolve, four-eyes, content-bound approvals | 4 | T-FLOW resolve rows; T-APPROVE-RACE | ☐ |
| S4 | Ownership changes several times | claim / release / assign / transfer, each an event; handoff track | 4, 10 | event tests; HandoffTrack screenshot | ☐ |
| S5 | Two people start on the same issue without realising | atomic claim; duplicate suggestions while typing and after creation | 4, 5, 6, 9 | T-CLAIM; similar-items test; E2E claim race | ☐ |
| S6 | Someone changes a request while another views or edits an older version | versions + If-Match + `changes_since`; live updates; conflict dialog | 3, 7, 10 | T-STALE; E2E stale edit | ☐ |
| S7 | Important requests disappear in message threads | one queue per team; "Needs an owner"; "going quiet" detection | 5 | attention tests | ☐ |
| S8 | Management can't see what's happening, who owns it, what needs attention, what changed, what's forgotten, or why a decision was made | dashboard; attention; activity timeline; "updated since you looked"; stale/overdue; decision log with required reasons | 5, 11 | stats/decisions tests; dashboard screenshot | ☐ |

## What the system should enable

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| E1 | Create and manage work items for investigation, action or resolution | six item types with defaults; full lifecycle | 3, 4 | API tests; E2E create | ☐ |
| E2 | Understand what the item is and why it exists | title, description, type, requester, created context | 3, 10 | detail screenshot | ☐ |
| E3 | Its current state and importance | status (shape + word), priority, SLA state | 3, 10 | detail screenshot | ☐ |
| E4 | Who is responsible | owner + team, handoff track | 4, 10 | detail screenshot | ☐ |
| E5 | What has happened previously | append-only timeline, decisions with reasons | 3, 5, 10 | event invariant test | ☐ |
| E6 | What requires attention next | `next_step` on every item; Inbox sections | 4, 5, 9 | next_step tests | ☐ |
| E7 | Different users on the same item at about the same time behave sensibly | CB1, CB2, CB3, CB5, CB8 | 3, 4, 10 | concurrency tests; two-user E2E | ☐ |

## Teams, identity and access

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| T1 | Multiple teams; a user may be in several with different responsibilities | memberships with a role per team | 1, 2 | policy matrix tests | ☐ |
| T2 | Not every user can perform every action | permission matrix SPEC §5.2 | 2, 4 | policy matrix tests | ☐ |
| T3 | Meaningful authorization model | team roles + resource rules (requester, assignee, confidential, four-eyes) | 2, 4 | policy matrix; T-VIS | ☐ |
| T4 | Enforced by the system, not only by hiding UI controls | server policy + SQL visibility clause; 404 for invisible | 2, 5, 11 | T-VIS; E2E viewer direct API 403 | ☐ |

## Collaboration and history

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| C1 | Users collaborate around work items | comments, watchers, notifications | 5, 6, 10 | comment tests; E2E | ☐ |
| C2 | Understand how an item evolved: responsibility, priority, workflow | typed events with from/to; handoff track; Decisions filter | 3, 4, 10 | event tests; screenshot | ☐ |
| C3 | Important actions don't silently disappear | one write path (`record_event`), same transaction, append-only trigger, reasons required for decisions | 1, 3 | event invariant test; trigger test | ☐ |

## Concurrent usage

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| K1 | Two users try to take responsibility for the same work | atomic conditional claim | 4 | T-CLAIM; E2E claim race | ☐ |
| K2 | One user views information another just changed | live updates; version guard; stale banner; If-Match on intent-dependent actions | 3, 7, 10 | SSE tests; E2E live update | ☐ |
| K3 | Multiple updates within a short period | row lock + versions (no lost updates); per-item client mutation queue; event coalescing | 3, 9, 10 | T-STALE; vitest scope test | ☐ |
| K4 | A user repeats an action, unsure if the first succeeded | idempotency keys in the same transaction; same key on retries | 3, 9 | T-IDEM; E2E double-submit | ☐ |
| K5 | Handle at least some deliberately | all four above | — | TESTING.md | ☐ |

## User experience

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| U1 | Useful overview of ongoing work | Inbox + Dashboard | 9, 11 | screenshots | ☐ |
| U2 | Quickly see what requires attention | attention sections; next-step chips | 5, 9 | attention tests | ☐ |
| U3 | Find relevant work without browsing everything | filters in URL, facets, full-text + fuzzy search, key jump, command palette | 5, 9 | search tests; E2E | ☐ |
| U4 | Usable as stored work grows | keyset pagination, virtualised list, indexes, ranked search capped | 3, 9, 12 | PERFORMANCE.md on large seed | ☐ |
| U5 | Coherent, responsive experience over decorative UI | DESIGN.md principles; performance budgets; designed states | 8–11 | screenshots; axe; budgets | ☐ |

## System behaviour

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| B1 | Secondary work (notifications, processing, enrichment) may be async | outbox worker: notifications, duplicate detection, SLA sweep, cleanup | 6 | worker tests | ☐ |
| B2 | Consider failure, running more than once, delay | retries with backoff, dead letters + retry UI, idempotent handlers, leases, advisory locks; UI never depends on async | 6, 11, 13 | T-OUTBOX; failure drills | ☐ |

## Expected scale

| ID | Brief says | Baton's answer | Phase | Proof | Status |
|---|---|---|---|---|---|
| X1 | Thousands of users, hundreds to a few thousand simultaneous, many teams, tens of thousands of active items, large growing history | large seed (2,000 users, 40 teams, 50,000 items, ~600,000 events); bench with concurrent users | 1, 12 | PERFORMANCE.md | ☐ |
| X2 | Must not load the entire dataset into the browser or application memory | keyset pagination everywhere, bounded queries, worker batches, bounded SSE queues | 3, 7, 9 | no-OFFSET check; query-plan test | ☐ |
| X3 | Design for continued growth | scaling path in ARCHITECTURE.md (partitioning, replicas, sequences, search engine) | 14 | ARCHITECTURE.md | ☐ |

## Engineering expectations (the ten named areas)

| ID | Area | Where it's shown | Proof | Status |
|---|---|---|---|---|
| G1 | Data modelling | SPEC §3; constraints; event log | schema tests; Decision #5 | ☐ |
| G2 | API design | commands vs PATCH, ETag/If-Match, problem+json, idempotency, keyset cursors | OpenAPI; Decision #3 | ☐ |
| G3 | Frontend state management | TanStack Query, version merge, mutation scopes, rebase | vitest suite | ☐ |
| G4 | Authorization | policy + visibility clause | policy matrix; T-VIS | ☐ |
| G5 | Concurrent operations | CB1–CB3, CB5 | concurrency tests | ☐ |
| G6 | Error handling | SPEC §12 mapping; timeouts; retries; designed error states | failure drills | ☐ |
| G7 | Data consistency | single transaction per command; outbox; DB constraints | event invariant; rollback test | ☐ |
| G8 | Search and filtering | FTS + trigram + facets + URL filters | search tests | ☐ |
| G9 | Application performance | indexes, budgets, virtualisation | PERFORMANCE.md | ☐ |
| G10 | Maintainability | pure domain modules, layered code, generated client, CI, docs | CI; layout | ☐ |
| G11 | Smaller system with well-considered behaviour over many incomplete features | explicit out-of-scope list | KNOWN_LIMITATIONS.md | ☐ |

## Critical behaviour (at least three beyond CRUD)

| ID | Brief example | Baton | Test | Status |
|---|---|---|---|---|
| CB1 | Simultaneous actions by multiple users | atomic claim | T-CLAIM | ☐ |
| CB2 | Handling stale information | versions + If-Match | T-STALE | ☐ |
| CB3 | Preventing accidental duplicate operations | idempotency keys | T-IDEM | ☐ |
| CB4 | Enforcing workflow rules | workflow module + DB constraints | T-FLOW | ☐ |
| CB5 | (beyond the examples) approvals bound to content | subject hash + invalidation | T-APPROVE-RACE | ☐ |
| CB6 | Authorization at the resource level | confidential, requester, four-eyes, 404 | T-VIS | ☐ |
| CB7 | Reliable asynchronous processing | outbox + SKIP LOCKED + idempotent handlers | T-OUTBOX | ☐ |
| CB8 | Reconciling optimistic frontend state with server decisions | version merge, scopes, rebase, rollback | T-RECONCILE + E2E | ☐ |
| CB-P | Be prepared to explain them | DEMO_SCRIPT.md | rehearsal done | ☐ |

## Submission

| ID | Brief asks for | Where | Status |
|---|---|---|---|
| D1 | Working source code | repository | ☐ |
| D2 | Clear instructions for running the application | README Quick start (verified from a clean clone) | ☐ |
| D3 | Any required setup instructions | README; `.env.example` | ☐ |
| D4 | Engineering decisions document (~5 decisions, trade-offs) | `docs/ENGINEERING_DECISIONS.md` | ☐ |
| D5 | Automated tests for important behaviour, reflecting the architecture's risks | `api/tests`, `web/src/**/*.test.ts`, `web/e2e`; `docs/TESTING.md` | ☐ |
| D6 | Brief description of known limitations | `docs/KNOWN_LIMITATIONS.md` | ☐ |
| D7 | Architecture diagrams or extra docs (welcome) | `docs/ARCHITECTURE.md` | ☐ |
| D8 | Important assumptions documented | README Assumptions (SPEC §1) | ☐ |

## Final discussion (prepared in DEMO_SCRIPT.md)

| ID | They'll ask | Prepared | Status |
|---|---|---|---|
| F1 | How your architecture works | ARCHITECTURE.md + 2-minute verbal walkthrough | ☐ |
| F2 | What happens when things fail | failure drills in TESTING.md | ☐ |
| F3 | What assumptions you made | README Assumptions | ☐ |
| F4 | Which parts you consider most important | CB1–CB8 ranked, with why | ☐ |
| F5 | What you'd change if it grew significantly | ARCHITECTURE.md scaling path; PERFORMANCE.md "what breaks first" | ☐ |
| F6 | What you'd do with another week | KNOWN_LIMITATIONS.md, ordered | ☐ |
| F7 | Explain or modify any part live | NOTES_FOR_INTERVIEW.md per phase; practised change requests | ☐ |
