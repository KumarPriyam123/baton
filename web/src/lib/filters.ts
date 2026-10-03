/**
 * Queue filters live in the URL (DESIGN §4.3, SPEC §8). `parseFilters` validates every field with
 * zod and drops what is invalid, so a hand-edited or stale link degrades to "fewer filters"
 * instead of an error page. `serializeFilters` writes a canonical form, so
 * parse(serialize(f)) equals f and serialize(parse(url)) is stable.
 */
import { z } from "zod";

export const STATUSES = [
  "new",
  "in_progress",
  "blocked",
  "awaiting_approval",
  "resolved",
  "closed",
] as const;
export const TYPES = [
  "incident",
  "customer_issue",
  "payment_investigation",
  "engineering",
  "compliance_request",
  "ops_task",
] as const;
export const SORTS = ["priority", "updated", "created", "due"] as const;

export type Status = (typeof STATUSES)[number];
export type ItemTypeValue = (typeof TYPES)[number];
export type Sort = (typeof SORTS)[number];

/** "Open" in DESIGN §4.3 is not an API status: it is these four together. */
export const OPEN_STATUSES: readonly Status[] = [
  "new",
  "in_progress",
  "blocked",
  "awaiting_approval",
];

export interface ListFilters {
  team?: string;
  status: Status[];
  priority: number[];
  type: ItemTypeValue[];
  assignee?: string;
  requester?: string;
  overdue?: true;
  q?: string;
  sort: Sort;
}

export const DEFAULT_SORT: Sort = "priority";

const uuid = z.uuid();
const fields = {
  team: z
    .string()
    .regex(/^[A-Za-z]{2,5}$/)
    .transform((v) => v.toUpperCase()),
  status: z.enum(STATUSES),
  priority: z.coerce.number().int().min(0).max(3),
  type: z.enum(TYPES),
  assignee: z.union([z.literal("me"), z.literal("none"), uuid]),
  requester: z.union([z.literal("me"), uuid]),
  q: z.string().trim().min(1).max(200),
  sort: z.enum(SORTS),
};

function many<T>(schema: z.ZodType<T>, values: string[]): T[] {
  const out: T[] = [];
  for (const value of values) {
    const parsed = schema.safeParse(value);
    if (parsed.success && !out.includes(parsed.data)) out.push(parsed.data);
  }
  return out;
}

function one<T>(schema: z.ZodType<T>, value: string | null): T | undefined {
  if (value === null) return undefined;
  const parsed = schema.safeParse(value);
  return parsed.success ? parsed.data : undefined;
}

export function parseFilters(params: URLSearchParams): ListFilters {
  const filters: ListFilters = {
    status: many(fields.status, params.getAll("status")).sort(
      (a, b) => STATUSES.indexOf(a) - STATUSES.indexOf(b),
    ),
    priority: many(fields.priority, params.getAll("priority")).sort((a, b) => a - b),
    type: many(fields.type, params.getAll("type")).sort(
      (a, b) => TYPES.indexOf(a) - TYPES.indexOf(b),
    ),
    sort: one(fields.sort, params.get("sort")) ?? DEFAULT_SORT,
  };
  const team = one(fields.team, params.get("team"));
  if (team) filters.team = team;
  const assignee = one(fields.assignee, params.get("assignee"));
  if (assignee) filters.assignee = assignee;
  const requester = one(fields.requester, params.get("requester"));
  if (requester) filters.requester = requester;
  if (params.get("overdue") === "true") filters.overdue = true;
  const q = one(fields.q, params.get("q"));
  if (q) filters.q = q;
  return filters;
}

/** Canonical order; defaults are left out so shared links stay short. */
export function serializeFilters(filters: ListFilters): URLSearchParams {
  const params = new URLSearchParams();
  if (filters.team) params.set("team", filters.team);
  for (const s of filters.status) params.append("status", s);
  for (const p of filters.priority) params.append("priority", String(p));
  for (const t of filters.type) params.append("type", t);
  if (filters.assignee) params.set("assignee", filters.assignee);
  if (filters.requester) params.set("requester", filters.requester);
  if (filters.overdue) params.set("overdue", "true");
  if (filters.q) params.set("q", filters.q);
  if (filters.sort !== DEFAULT_SORT) params.set("sort", filters.sort);
  return params;
}

export function emptyFilters(): ListFilters {
  return { status: [], priority: [], type: [], sort: DEFAULT_SORT };
}

/** True when nothing narrows the list (sort alone doesn't count). */
export function hasNarrowing(filters: ListFilters): boolean {
  return (
    filters.team !== undefined ||
    filters.status.length > 0 ||
    filters.priority.length > 0 ||
    filters.type.length > 0 ||
    filters.assignee !== undefined ||
    filters.requester !== undefined ||
    filters.overdue === true ||
    filters.q !== undefined
  );
}

/** What the API accepts on GET /items: arrays repeat, `sort` is omitted for ranked search. */
export function toApiQuery(filters: ListFilters) {
  return {
    ...(filters.team ? { team: filters.team } : {}),
    ...(filters.status.length ? { status: filters.status } : {}),
    ...(filters.priority.length ? { priority: filters.priority } : {}),
    ...(filters.type.length ? { type: filters.type } : {}),
    ...(filters.assignee ? { assignee: filters.assignee } : {}),
    ...(filters.requester ? { requester: filters.requester } : {}),
    ...(filters.overdue ? { overdue: true } : {}),
    ...(filters.q ? { q: filters.q } : { sort: filters.sort }),
  };
}

export function isOpenSet(statuses: readonly Status[]): boolean {
  return (
    statuses.length === OPEN_STATUSES.length && OPEN_STATUSES.every((s) => statuses.includes(s))
  );
}
