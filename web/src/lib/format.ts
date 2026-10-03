import { format } from "date-fns";

/** "12m", "5h", "3d": the strip's age column (DESIGN §4.1). */
export function ageShort(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.floor((now - new Date(iso).getTime()) / 1000));
  if (seconds < 60) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${String(minutes)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${String(hours)}h`;
  const days = Math.floor(hours / 24);
  if (days < 60) return `${String(days)}d`;
  return `${String(Math.floor(days / 30))}mo`;
}

/** Full local time for tooltips. The server sends UTC; the browser formats it. */
export function fullTime(iso: string): string {
  return format(new Date(iso), "d MMM yyyy, HH:mm");
}

export const STATUS_LABEL = {
  new: "New",
  in_progress: "In progress",
  blocked: "Blocked",
  awaiting_approval: "Awaiting approval",
  resolved: "Resolved",
  closed: "Closed",
} as const;

export const TYPE_LABEL = {
  incident: "Incident",
  customer_issue: "Customer issue",
  payment_investigation: "Payment investigation",
  engineering: "Engineering",
  compliance_request: "Compliance request",
  ops_task: "Ops task",
} as const;

export const PRIORITY_LABEL = ["P0", "P1", "P2", "P3"] as const;

/** Due date for a priority, from SPEC §4.2: P0 4 h, P1 24 h, P2 3 days, P3 7 days. */
export const DUE_PREVIEW = [
  "Due in 4 hours",
  "Due in 24 hours",
  "Due in 3 days",
  "Due in 7 days",
] as const;

export function plural(count: number, one: string, many: string): string {
  return `${String(count)} ${count === 1 ? one : many}`;
}
