/**
 * Event → words, for the timeline and for "Priya changed the priority a moment ago".
 * Pure: names for ids come from the `names` map the caller builds (actors, item people, members).
 */
import type { components } from "../api/generated";
import { PRIORITY_LABEL, STATUS_LABEL, TYPE_LABEL } from "./format";

export type EventOut = components["schemas"]["EventOut"];
export type EventKind = components["schemas"]["EventKind"];

export interface NameLookup {
  person: (id: unknown) => string;
  team: (id: unknown) => string;
}

function to(data: EventOut["data"], field: string): unknown {
  const value = data[field];
  return value && typeof value === "object" && "to" in value ? value.to : undefined;
}

function from(data: EventOut["data"], field: string): unknown {
  const value = data[field];
  return value && typeof value === "object" && "from" in value ? value.from : undefined;
}

const firstName = (name: string): string => name.split(/\s+/)[0] ?? name;

export function actorName(event: Pick<EventOut, "actor">): string {
  return event.actor ? event.actor.name : "Baton";
}

/** One sentence about what happened, without the actor ("assigned this to Rahul"). */
export function describeEvent(event: EventOut, names: NameLookup): string {
  const { data } = event;
  switch (event.kind) {
    case "created":
      return "raised this request";
    case "field_changed": {
      const fields = ["title", "description", "type", "due_at"].filter((f) => f in data);
      const words = fields.map((f) => (f === "due_at" ? "due date" : f));
      if (fields.includes("type")) {
        const t = to(data, "type");
        if (typeof t === "string" && t in TYPE_LABEL) {
          return `changed the type to ${TYPE_LABEL[t as keyof typeof TYPE_LABEL]}`;
        }
      }
      return `changed the ${words.join(" and ") || "request"}`;
    }
    case "priority_changed": {
      const p = to(data, "priority");
      const label = typeof p === "number" ? (PRIORITY_LABEL[p] ?? String(p)) : "";
      return label ? `changed the priority to ${label}` : "changed the priority";
    }
    case "assigned": {
      const assignee = to(data, "assignee");
      const previous = from(data, "assignee");
      const who = names.person(assignee);
      if (!previous && event.actor && event.actor.id === assignee) return "took this";
      if (previous) return `reassigned this from ${names.person(previous)} to ${who}`;
      return `assigned this to ${who}`;
    }
    case "unassigned":
      return `unassigned ${names.person(from(data, "assignee"))}`;
    case "status_changed": {
      const s = to(data, "status");
      return typeof s === "string" && s in STATUS_LABEL
        ? `moved this to ${STATUS_LABEL[s as keyof typeof STATUS_LABEL]}`
        : "changed the status";
    }
    case "blocked":
      return "marked this blocked";
    case "unblocked":
      return "unblocked this";
    case "resolved":
      return "resolved this";
    case "reopened":
      return "reopened this";
    case "closed":
      return "closed this";
    case "transferred":
      return `moved this from ${names.team(from(data, "team"))} to ${names.team(to(data, "team"))}`;
    case "approval_requested":
      return "requested approval";
    case "approval_approved":
      return "approved this";
    case "approval_rejected":
      return "rejected the approval";
    case "approval_cancelled":
      return "cancelled the approval request";
    case "approval_invalidated": {
      const fields = Array.isArray(data.fields) ? (data.fields as string[]).join(" and ") : "";
      return fields
        ? `Approval withdrawn: the ${fields} changed`
        : "Approval withdrawn: the content changed";
    }
    case "requires_approval_changed":
      return to(data, "requires_approval") === true
        ? "turned on required approval"
        : "turned off required approval";
    case "confidential_changed":
      return to(data, "confidential") === true
        ? "marked this confidential"
        : "removed confidential";
    case "commented":
      return "commented";
    case "sla_breached":
      return "passed its due date";
    case "duplicate_suggested":
      return "found a possible duplicate";
  }
}

/** The short verb phrase for `last_event` (no data available): "Priya changed the priority". */
const LAST_EVENT_PHRASE: Record<EventKind, string> = {
  created: "raised this",
  field_changed: "edited this",
  priority_changed: "changed the priority",
  assigned: "changed the owner",
  unassigned: "unassigned this",
  status_changed: "changed the status",
  blocked: "marked this blocked",
  unblocked: "unblocked this",
  resolved: "resolved this",
  reopened: "reopened this",
  closed: "closed this",
  transferred: "moved this to another team",
  approval_requested: "requested approval",
  approval_approved: "approved this",
  approval_rejected: "rejected the approval",
  approval_cancelled: "cancelled the approval",
  approval_invalidated: "withdrew the approval",
  requires_approval_changed: "changed the approval requirement",
  confidential_changed: "changed confidentiality",
  commented: "commented",
  sla_breached: "passed its due date",
  duplicate_suggested: "found a possible duplicate",
};

/** The short verb phrase of an event kind: "approved this", "changed the priority". */
export function eventPhrase(kind: EventKind): string {
  return LAST_EVENT_PHRASE[kind];
}

export function lastEventSentence(event: {
  kind: EventKind;
  actor: { name: string } | null;
}): string {
  const who = event.actor ? firstName(event.actor.name) : "Baton";
  return `${who} ${LAST_EVENT_PHRASE[event.kind]} a moment ago.`;
}

export { firstName };
