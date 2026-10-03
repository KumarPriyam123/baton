/**
 * Automatic rebase on 412 (SPEC §6.6, DESIGN §6.2).
 *
 * I sent a change on version N; the server is at N+k and tells me what happened in between
 * (`changes_since`). If none of the fields I changed were touched by those events, my change is
 * still what I meant, so it goes again on the new version and I'm told it was merged. If any
 * field overlaps, only a person can decide: that is the conflict dialog.
 *
 * Nothing here knows workflow rules. It reads field names out of event `data` ({field: {from, to}})
 * and compares them with the field names of my own request.
 */
import { ApiError } from "./api";

export interface ChangeEvent {
  kind?: string;
  actor?: { name: string } | null;
  data?: Record<string, unknown>;
}

/** Event kinds whose `data` isn't a `{field: {from, to}}` map. */
const NON_FIELD_KINDS = new Set(["approval_invalidated", "commented", "sla_breached"]);

/** Every field named in the `data` of these events. */
export function fieldsChangedBy(changes: readonly ChangeEvent[]): string[] {
  const fields = new Set<string>();
  for (const change of changes) {
    if (change.kind && NON_FIELD_KINDS.has(change.kind)) continue;
    for (const [field, value] of Object.entries(change.data ?? {})) {
      if (value && typeof value === "object" && "to" in value) fields.add(field);
    }
  }
  return [...fields];
}

export function authorsOf(changes: readonly ChangeEvent[]): string[] {
  const names = new Set<string>();
  for (const change of changes) if (change.actor?.name) names.add(change.actor.name);
  return [...names];
}

export type RebasePlan =
  | { kind: "resend"; theirFields: string[]; authors: string[] }
  | { kind: "conflict"; overlap: string[]; theirFields: string[]; authors: string[] };

/**
 * `changes` undefined means the server didn't say what changed: we can't prove the edits are
 * disjoint, so it's a conflict.
 */
export function planRebase(
  mine: readonly string[],
  changes: readonly ChangeEvent[] | undefined,
): RebasePlan {
  if (changes === undefined) {
    return { kind: "conflict", overlap: [...mine], theirFields: [], authors: [] };
  }
  const theirFields = fieldsChangedBy(changes);
  const authors = authorsOf(changes);
  const overlap = mine.filter((field) => theirFields.includes(field));
  return overlap.length === 0
    ? { kind: "resend", theirFields, authors }
    : { kind: "conflict", overlap, theirFields, authors };
}

const FIELD_WORD: Record<string, string> = {
  title: "title",
  description: "description",
  priority: "priority",
  type: "type",
  due_at: "due date",
  confidential: "confidentiality",
  requires_approval: "approval requirement",
  status: "status",
  assignee: "owner",
  team: "team",
};

export function fieldWord(field: string): string {
  return FIELD_WORD[field] ?? field.replace(/_/g, " ");
}

function joinWords(words: readonly string[]): string {
  if (words.length <= 1) return words[0] ?? "";
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1] ?? ""}`;
}

/** "Saved. Asha's change to priority was kept." */
export function describeKept(plan: { theirFields: string[]; authors: string[] }): string {
  const who = plan.authors.length > 0 ? joinWords(plan.authors) : "Someone";
  const possessive = plan.authors.length > 1 ? `${who}'` : `${who}'s`;
  const fields = plan.theirFields.map(fieldWord);
  const what = fields.length > 0 ? ` to ${joinWords(fields)}` : "";
  return `Saved. ${possessive} change${what} was kept.`;
}

export type RebaseOutcome<T> =
  | { kind: "saved"; result: T; kept?: { theirFields: string[]; authors: string[] } }
  | { kind: "conflict"; plan: Extract<RebasePlan, { kind: "conflict" }>; error: ApiError };

/**
 * Runs `send(version)` and handles a 412 once or twice:
 * disjoint fields → send again on `error.current.version` (the caller makes a new
 * Idempotency-Key per send, decision 34); overlapping → return the conflict for the dialog.
 * Any other error propagates.
 */
export async function runWithRebase<T>(
  mine: readonly string[],
  startVersion: number,
  send: (version: number) => Promise<T>,
  maxResends = 2,
): Promise<RebaseOutcome<T>> {
  let version = startVersion;
  let kept: { theirFields: string[]; authors: string[] } | undefined;
  for (let attempt = 0; ; attempt += 1) {
    try {
      const result = await send(version);
      return kept ? { kind: "saved", result, kept } : { kind: "saved", result };
    } catch (error) {
      if (
        !(error instanceof ApiError) ||
        error.status !== 412 ||
        error.code !== "VERSION_CONFLICT"
      ) {
        throw error;
      }
      const plan = planRebase(mine, error.changesSince);
      if (plan.kind === "conflict" || !error.current || attempt >= maxResends) {
        return {
          kind: "conflict",
          plan:
            plan.kind === "conflict"
              ? plan
              : {
                  kind: "conflict",
                  overlap: [],
                  theirFields: plan.theirFields,
                  authors: plan.authors,
                },
          error,
        };
      }
      version = error.current.version;
      kept = {
        theirFields: [...new Set([...(kept?.theirFields ?? []), ...plan.theirFields])],
        authors: [...new Set([...(kept?.authors ?? []), ...plan.authors])],
      };
    }
  }
}
