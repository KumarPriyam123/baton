# Baton — Design Spec

How Baton looks, moves and speaks. The brief says: *"We care significantly more about a coherent and responsive experience than decorative UI."* Every rule below serves that.

## 1. Concept: the dispatch desk

Baton is a desk people work from all day, under pressure. The visual language comes from two things in that world:

- **Flight progress strips** — controllers track each aircraft on a narrow strip with a coloured edge and scan a rack of them in seconds. Baton's list rows are strips: one line, a priority edge, the next step stated in words.
- **The relay baton** — work is handed from person to person. Baton shows that handoff trail on every item as the **handoff track**. This is the one memorable element; everything else stays quiet.

| Principle | In practice |
|---|---|
| Scan first, read second | One-line strips; the next step is written out ("Needs an owner"), never left to decode from colour |
| Signal colour is reserved | Red, amber and steel only ever mean priority or attention. The interactive accent is a different hue (Dispatch green) |
| The server's answer is what the screen shows | Pending states are visible; a lost race is explained ("Asha took this 4 seconds ago"), never silently overwritten |
| Spend boldness once | The handoff track gets motion and colour. Lists, panels and forms are plain |

**Not this:** cream backgrounds, dark-mode-first neon, grids of identical rounded cards, ALL-CAPS labels over every heading, `A · B · C` meta strings, monospace for small labels, arrows appended to buttons.

---

## 2. Tokens

Defined once as CSS variables in `web/src/styles/tokens.css` and exposed to Tailwind v4 through `@theme`. Components use tokens, never raw hex.

### 2.1 Colour — light ("day desk", default)

| Token | Hex | Use | Contrast on Sheet |
|---|---|---|---|
| `--desk` | `#EEF1EE` | app background, rail | — |
| `--sheet` | `#FFFFFF` | lists, panels, dialogs | — |
| `--ink` | `#18212B` | primary text | 16.3 : 1 |
| `--pencil` | `#5A6672` | secondary text, metadata | 5.9 : 1 |
| `--rule` | `#D9DED9` | hairlines, borders | — |
| `--dispatch` | `#0B6E5F` | primary buttons, links, focus, selection | 6.2 : 1 (white on it 6.2 : 1) |
| `--dispatch-wash` | `#E3F1EE` | selected row, hover on primary surfaces | — |
| `--p0` | `#C42B1C` | P0 edge and text, errors | 5.7 : 1 |
| `--p1` | `#F2A93B` (edge/fill) · `#94570A` (text) | P1 | text 5.8 : 1 |
| `--p2` | `#3D6B99` | P2 edge and text | 5.6 : 1 |
| `--p3` | `#66707A` | P3 text (no edge) | ≈ 5 : 1 |

### 2.2 Colour — dark ("night desk", follows system, toggle in the user menu)

| Token | Hex |
|---|---|
| `--desk` | `#14191E` |
| `--sheet` | `#1B2127` |
| `--ink` | `#E6EAED` |
| `--pencil` | `#9AA5AF` |
| `--rule` | `#2A3239` |
| `--dispatch` | `#3FB39B` (text on it `#0E1A17`) |
| `--dispatch-wash` | `#1D2F2B` |
| `--p0` `--p1` `--p2` `--p3` | `#FF7A6E` · `#F2A93B` · `#86AEDA` · `#8C96A0` |

All text pairs were checked at ≥ 4.5 : 1. Phase 13 re-checks them with axe.

### 2.3 Type

**Atkinson Hyperlegible Next** (variable, self-hosted). It was designed by the Braille Institute so that easily confused characters are distinct (I/l/1, O/0, rn/m). That matters when people read keys like `PAY-1O1` against `PAY-101` at speed. One family for everything; hierarchy comes from size and weight.

- Self-host from `@fontsource` if the package exists, otherwise download the woff2 files into `web/public/fonts`. No runtime Google Fonts request.
- Use `font-variant-numeric: tabular-nums` for keys, counts, times and durations. If the font lacks tabular figures, keep the same face anyway; don't switch to a monospace.
- Fallback stack: `"Atkinson Hyperlegible Next", "Segoe UI", system-ui, sans-serif`.

| Role | Size / line height | Weight |
|---|---|---|
| Dashboard figure | 32 / 36 | 650 |
| Page title | 24 / 30 | 650 |
| Item title (detail) | 20 / 26 | 650 |
| Section heading | 15 / 20 | 650 |
| Body, list title | 14 / 20 | 450 (list title 550) |
| Secondary, metadata | 13 / 18 | 450 |
| Small (chips, timestamps) | 12 / 16 | 550 |

Sentence case everywhere. Section headings are plain words in `--ink`; there are no labels above headings. Headings use `letter-spacing: -0.01em`.

### 2.4 Space, shape, depth

| Token | Value |
|---|---|
| Spacing scale | 4 · 8 · 12 · 16 · 24 · 32 · 48 |
| Radius | 4 px chips and inputs · 8 px panels, popovers, dialogs · **0 for list strips** (full-bleed rows) |
| Borders | 1 px `--rule`; no borders inside lists except the hairline between strips |
| Shadow | only on floating layers (popover, dialog, drawer): `0 8px 24px rgb(16 24 32 / 0.14)` |
| Focus ring | 2 px `--dispatch`, 2 px offset, on every focusable element |

### 2.5 Motion

| What | Duration | Easing |
|---|---|---|
| Hover/press feedback | 100 ms, colour only | linear |
| Drawer, popover, dialog | 160 ms, opacity + 8 px translate | `cubic-bezier(.2,.8,.2,1)` |
| Handoff track: new segment grows in when ownership changes | 220 ms | same |
| Row that changed live | background fades from `--dispatch-wash` to none over 1.2 s | ease-out |

No entrance animations on page load. No looping animations except the pending spinner on a button. With `prefers-reduced-motion`, all of the above become instant.

### 2.6 Icons and glyphs

Lucide, 16 px, stroke 1.75. **Status always pairs a shape with its word**, so colour is never the only signal:

| Status | Icon | Word |
|---|---|---|
| new | `CircleDashed` | New |
| in_progress | `CircleDot` | In progress |
| blocked | `CircleSlash` | Blocked |
| awaiting_approval | `Hourglass` | Awaiting approval |
| resolved | `CircleCheck` | Resolved |
| closed | `CircleX` (muted) | Closed |

**Priority** = 4 px left edge in the strip **and** a bar glyph with a text label in the detail view (P0 four bars … P3 one bar). P3 has no edge.

**Avatars**: initials on one of 8 muted hues chosen from the user id; each hue is checked for white-text contrast. An unowned item shows a dashed empty circle.

---

## 3. Layout

### 3.1 App shell (≥ 1280 px)

```
┌───────────────┬──────────────────────────────────────┬────────────────────────────┐
│ Baton         │  Search requests  /      Filters ▾   [New request]  (top bar)     │
│               ├──────────────────────────────────────┼────────────────────────────┤
│ Inbox      12 │ ▌PAY-142  Refund stuck for order…    │ PAY-142                    │
│ My work     5 │           Needs your approval  AR 2h │ Refund stuck for order     │
│ My requests 3 │ ▌SRE-88   p99 latency on checkout…   │ 48213                      │
│               │           Overdue by 3 h       RK 5h │ ◐ In progress  ▮▮▮ P1      │
│ Teams         │ ▌SUP-911  Customer can't reset 2FA   │ ─ handoff track ─────────  │
│  Payments     │           Needs an owner       ◌ 12m │ [Approve] [Reject] …       │
│  Support      │                                      │ Properties │ Timeline      │
│  Platform     │  (virtualised, infinite)             │                            │
│               │                                      │                            │
│ Dashboard     │                                      │                            │
│ Decisions     │                                      │                            │
│               │                                      │                            │
│ 🔔 3   Priya ▾│                                      │                            │
└───────────────┴──────────────────────────────────────┴────────────────────────────┘
   rail 232 px         queue: flexible (min 480 px)          detail: 560 px
```

- The rail sits on `--desk`; queue and detail sit on `--sheet`, separated by a 1 px rule. There are no cards inside the queue.
- Selecting a strip opens its detail on the right and puts its key in the URL (`/items/PAY-142?…filters`), so links and the back button work.
- Content is left-aligned throughout. Numbers in tables are right-aligned.

### 3.2 Breakpoints

| Width | Behaviour |
|---|---|
| ≥ 1280 | rail + queue + detail side by side |
| 1024–1279 | detail opens as a drawer over the queue (`Esc` closes it) |
| 768–1023 | rail collapses to icons with tooltips |
| < 768 | one column: queue, then a full detail page; primary actions in a bottom bar; touch targets ≥ 44 px |

---

## 4. Screens

Each screen lists its content, its states, and the key interactions. Every screen has loading, empty and error states; none show a blank area.

### 4.1 Strip (list row) — used everywhere

```
▌ PAY-142  ◐ Refund stuck for order 48213          Needs your approval   (AR)   2h  •
  │  key    status + title (truncate)                next step chip        owner  age unread
  └ priority edge (P0 red, P1 amber, P2 steel, P3 none)
```

- 44 px tall (comfortable) or 36 px (compact, toggle in the user menu).
- The next-step chip uses severity: high = `--p0` text on a tinted background; medium = `--p1` text; low = `--pencil`.
- The age column is relative ("12m", "5h", "3d"), with the full time in a tooltip.
- The dot at the far right means "updated since you last opened it".
- Hover reveals two quick actions on the right (Assign to me, Watch); the keyboard equivalents always work.
- A strip that changes live briefly washes `--dispatch-wash` (§2.5). It does **not** jump position while you're looking; a small "3 items changed — Refresh order" bar appears at the top of the list instead.

### 4.2 Inbox (default page)

Sections from `GET /me/attention` in this order: Needs your approval · Yours, overdue or urgent · Needs an owner · Yours, going quiet · Your requests. Each section has a heading with its count, up to 10 strips, and "Show all" (opens the queue with the matching filter).

| State | Shows |
|---|---|
| Loading | 3 section headings with 3 skeleton strips each |
| Section empty | the section is hidden |
| All empty | "Nothing needs you right now. New requests for Payments and Platform will show up here." + [New request] |
| Error | "Couldn't load your inbox. [Try again]" + request reference |

### 4.3 Queue (all items, filtered)

- **Filter bar**: removable tokens (`Team: Payments ✕`, `Status: Open ✕`, `Priority: P0–P1 ✕`), an "Add filter" menu whose options show facet counts (`Blocked 14`), and a sort menu. All of it lives in the URL.
- Quick views in the rail map to filter presets: My work = `assignee=me&status=open`; My requests = `requester=me`; a team = `team=PAY&status=open`.
- **Search**: typing in the top bar searches as you type (debounced 200 ms). A key such as `PAY-142` jumps straight to the item. Results show the matched words highlighted in the title.
- Infinite scroll, virtualised; the next page loads 10 rows before the end.

| State | Shows |
|---|---|
| No results for filters | "No requests match these filters." + [Clear filters] |
| No search results | "No requests match "refnd". Check the spelling or search all teams." |

### 4.4 Item detail

Top to bottom:

1. **Header**: key (pencil, tabular), title (20 px), then a row of status, priority, type, team and a "Confidential" lock when set.
2. **Handoff track** (§5).
3. **Action bar**: rendered **only** from `allowed_actions`. The primary button follows `next_step` (unowned → **Assign to me**; awaiting your approval → **Approve**; in progress and yours → **Resolve**). Secondary actions sit in a "More" menu. Actions that need a reason open a small popover with a required text field.
4. **Approval panel** (only when relevant): what was approved and against which version, e.g. "Approved by Priya at 11:02. The description has changed since, so this approval no longer counts." [Request approval again]
5. **Two columns** (single column under 1280 px):
   - Left, *Timeline*: events and comments interleaved, newest at the bottom. Filter: All · Comments · Decisions. Decisions show the reason as a quotation. A divider marks "New since you last looked". The composer is pinned at the bottom (`⌘/Ctrl + Enter` sends).
   - Right, *Properties*: Status, Priority, Owner, Team, Requester, Due (with "Overdue by 3 h" in `--p0`), Type, Requires approval, Watchers, Created, Updated. Editable properties open inline pickers; each edit is one request.
6. **Description**: rendered Markdown (no raw HTML). Editing switches to a textarea with a Preview tab and keeps a local draft.

### 4.5 New request (dialog, `c`)

Fields in order: Team (searchable) · Type (segmented, with icons) · Title · Description (Markdown, Preview tab) · Priority (default P2; shows "Due in 3 days") · Confidential and Requires approval (pre-set from type, with one line saying why).

While you type the title, **Similar open requests** appear under it:

> PAY-131 Refund stuck for order 48211 — in progress with Asha  [Open] [Watch instead]

This answers "two people start working on the same issue without realising it" before the duplicate exists.

The submit button reads **Create request**. While it's pending it shows a spinner and ignores further clicks; the idempotency key protects the server anyway. On success: toast "Created PAY-143" with [Open].

### 4.6 Dashboard

A **table**, not a grid of cards, because the job is comparing teams.

```
Open 1,284     Overdue 37     Unowned P0–P1 9     Awaiting approval 14         (whole org, figures 32 px)

Team        Open by priority          Unowned  Overdue  Awaiting  Median age  14 days (created / resolved)
Payments    ███▆▃  212                      4        11         6       1.8d   ╱╲╱‾╲_  
Support     ██▅▃▂  341                      2         9         0       0.9d   ‾╲_╱‾╲
…
```

Every number is a link to the queue with that filter. The 5 oldest open items per team appear in an expandable row. Charts follow the dataviz rules: one hue per series, direct labels, no legends where a label will do.

### 4.7 Decision log

Newest first: who decided what, the reason as a quotation, and the item. Filter by team and kind (approvals, rejections, closes, reopens, priority downgrades, transfers, approval requirement removed).

### 4.8 Team settings

Members table: name, email, role (select), added. Leads can add members and viewers and change those roles; only admins can make or remove leads. Removing a member who owns open items shows how many items they own and asks the lead to reassign them first or confirm they'll return to "Needs an owner".

### 4.9 Jobs (admin)

Tabs: Pending · Dead · Recently done. Row: topic, item key, attempts, next attempt, last error (expandable). Dead rows have [Retry]. An empty Dead tab says "No failed jobs. Notifications and checks are running normally."

### 4.10 Sign in

A centred panel on `--desk`: "Baton", one line ("Coordinate operational work across teams."), email, password, [Sign in]. In demo mode a list below: "Sign in as Priya Nair — Payments lead", "…Asha Rao — Payments member", "…Dev Malhotra — viewer", "…Admin". Errors: "Email or password is incorrect." / "Too many attempts. Try again at 14:32."

### 4.11 Notifications

Bell in the rail footer with the unread count. Popover lists notifications grouped by item: key and title on one line, then one line per event ("Priya approved this" with the age right-aligned in its own column), [Mark all read]. Opening an item marks its notifications read.

### 4.12 Command palette (`⌘/Ctrl + K`)

Jump to an item by key or title, run an action on the selected item ("Assign to me", "Set priority P1"), navigate (Inbox, Dashboard, a team), create a request.

---

## 5. The handoff track

The one place Baton spends its boldness. It answers four of the brief's questions at a glance: who is responsible, how ownership changed, how long each person held it, and what's happening now.

```
 Meera raised it      Payments queue          Asha                       Rahul (now)
 ●────────────────────○─ ─ ─ ─ ─ ─ ─ ─────────●══════════════════════════◉══════════
 09:14                09:14 → 09:36 (22m)     09:36 → 11:46 (2h 10m)     11:46 → now
```

- A thin baseline; each segment is a period of ownership. Unowned periods are dashed. The current holder's segment is thicker, in `--dispatch`, ending in a ringed avatar.
- Segment width ∝ log(duration), with a minimum width, so a 3-day hold doesn't crush a 5-minute one.
- Each handoff point shows the avatar. Hover or focus shows a tooltip: "Priya (lead) handed this from Asha to Rahul at 11:46: *Asha is out today*."
- Built from `assigned`/`unassigned`/`transferred` events. More than 6 handoffs collapse the middle into "+4".
- When ownership changes live, the new segment grows in (220 ms); this is the only decorative motion in the app.
- Accessible version: a visually hidden ordered list with the same information.

---

## 6. Interaction rules

### 6.1 Feedback for every action

| Kind | Immediate | On success | On failure |
|---|---|---|---|
| Optimistic (watch, comment, priority) | UI changes at once; comment shows "Sending…" | quiet; cache replaced with the server item | rolls back + toast with the reason |
| Confirmed (claim, approve, resolve, transitions) | button shows spinner, other actions disabled | toast with the outcome ("Assigned to you") | explained toast; item refreshed |

Toasts use the same verb as the button: Assign to me → "Assigned to you"; Request approval → "Approval requested"; Move to team → "Moved to Support".

### 6.2 Conflicts and stale data — the copy

| Situation | What the user sees |
|---|---|
| Lost a claim | Toast: "Asha took PAY-142 4 seconds ago." [View] — the strip and detail show Asha as owner |
| Item changed while viewing | Detail bar: "Priya changed the priority to P1 a moment ago." Fields update in place with the wash highlight |
| Item changed while editing | Banner above the editor: "Asha changed the description while you were editing. Your draft is kept." [Review changes] [Keep editing] |
| Save hits 412, different fields | Saves automatically on the new version; toast: "Saved. Asha's change to priority was kept." |
| Save hits 412, same field | Dialog "This request changed while you were editing": *Saved now* vs *Your draft*, word-level diff. [Save my version] [Discard my draft]. Note: "Saving yours replaces Asha's description." |
| Approve on a stale view | "PAY-142 changed since you opened it. Review the changes before approving." [Show changes] → diff → [Approve this version] |
| Approval invalidated | Approval panel: "Approval withdrawn: the description changed after Priya approved it. Request approval again before resolving." |
| Item moved away / access lost | "PAY-142 moved to Support. You no longer have access to it." Detail closes; the strip leaves the list |
| Forbidden | Toast: the server's reason, e.g. "Only Payments leads can approve." |
| Busy after retries | "Baton is busy and couldn't save this. Try again in a moment." |
| Server error | "Something went wrong on our side. Nothing was saved. Reference req_8f2c…" |
| Offline | Top banner: "You're offline. Actions are paused until you reconnect." Buttons that write are disabled |

Errors don't apologise and always say what happened and what to do.

### 6.3 Keyboard

| Key | Action |
|---|---|
| `/` | focus search |
| `c` | new request |
| `j` / `k` | next / previous strip (prefetches its detail) |
| `Enter`, `Esc` | open, close detail |
| `a` | assign to me |
| `p` then `0`–`3` | set priority |
| `s` | status / workflow menu |
| `m` | focus the comment box |
| `⌘/Ctrl + K` | command palette |
| `g` then `i` / `q` / `d` | go to Inbox / Queue / Dashboard |
| `?` | shortcut help |

Shortcuts are ignored while typing in a field.

---

## 7. Accessibility checklist

- Radix primitives for dialog, popover, menu, select, tooltip, tabs (focus trapping, Esc, ARIA).
- Everything reachable and operable by keyboard; visible focus ring.
- Status, priority and severity never rely on colour alone (shape + word).
- `aria-live="polite"` region announces live changes ("PAY-142 updated by Asha") and toasts; conflicts use `assertive`.
- Contrast ≥ 4.5 : 1 for text, ≥ 3 : 1 for UI edges that carry meaning.
- Respects `prefers-reduced-motion` and `prefers-color-scheme`.
- Phase 13 runs axe in Playwright on Inbox, Queue, Detail, New request and Dashboard: no serious or critical violations.

## 8. Components to build (in `web/src/components/ui`)

Button (primary, secondary, quiet, danger; pending state) · IconButton · Input · Textarea with Preview · Select / Combobox · Menu · Popover · Dialog · Drawer · Tabs · Tooltip · Toast (Sonner, themed) · Avatar / AvatarStack · StatusLabel · PriorityGlyph · NextStepChip · Strip · Skeleton · EmptyState · ErrorState · FilterToken · KeyboardHint · HandoffTrack · DiffView · Sparkline · Kbd.

A dev-only route `/dev/ui` shows every component in every state, in both themes. Phase 8 uses it to check the design before any screen is built.
