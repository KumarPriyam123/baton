/**
 * Builds the handoff track (DESIGN §5) from the item's events. Pure: no I/O, no React.
 *
 * Only `created`, `assigned`, `unassigned` and `transferred` events matter. Each period is a
 * segment: a person holding the item, or nobody (dashed). The last segment ends now, or when the
 * item was resolved or closed.
 */
import type { EventOut, NameLookup } from "../../lib/eventText";
import { actorName, firstName } from "../../lib/eventText";

export interface Person {
  id: string;
  name: string;
}

export interface Segment {
  /** Stable across renders: the id of the event that started it. */
  id: string;
  holder: Person | null;
  /** "Meera" for a person; "Payments queue" for an unowned period. */
  label: string;
  start: number;
  end: number;
  current: boolean;
  /** What the handoff point says, for the tooltip: "Priya handed this from Asha to Rahul". */
  handoff: string;
  reason: string | null;
  /** How many segments were folded into this one (collapsed middle). 0 for a real segment. */
  collapsed: number;
}

export interface TrackInput {
  events: readonly EventOut[]; // oldest first
  requester: Person;
  teamName: string;
  names: NameLookup;
  resolveName: (id: string) => Person;
  createdAt: string;
  endedAt: string | null;
  now: number;
  status: string;
}

const MAX_SEGMENTS = 6;

export function buildTrack(input: TrackInput): Segment[] {
  const { events, names, resolveName } = input;
  const created = events.find((e) => e.kind === "created");
  const startAt = new Date(created?.created_at ?? input.createdAt).getTime();
  const endAt = input.endedAt ? new Date(input.endedAt).getTime() : input.now;

  const raised: Segment = {
    id: `e${String(created?.id ?? 0)}`,
    holder: null,
    label: `${input.teamName} queue`,
    start: startAt,
    end: endAt,
    current: true,
    handoff: `${input.requester.name} raised it`,
    reason: null,
    collapsed: 0,
  };
  const segments: Segment[] = [raised];

  const open = (event: EventOut, holder: Person | null, label: string, handoff: string): void => {
    const at = new Date(event.created_at).getTime();
    const last = segments[segments.length - 1];
    if (last) {
      last.end = at;
      last.current = false;
    }
    segments.push({
      id: `e${String(event.id)}`,
      holder,
      label,
      start: at,
      end: endAt,
      current: true,
      handoff,
      reason: event.reason,
      collapsed: 0,
    });
  };

  let teamName = input.teamName;
  const createdTeam = created ? toId(created.data, "team") : undefined;
  if (createdTeam) teamName = names.team(createdTeam);
  raised.label = `${teamName} queue`;

  for (const event of events) {
    const who = actorName(event);
    switch (event.kind) {
      case "assigned": {
        const id = toId(event.data, "assignee");
        if (!id) break;
        const holder = resolveName(id);
        const before = fromId(event.data, "assignee");
        const self = event.actor?.id === holder.id;
        let handoff: string;
        if (before) {
          handoff = `${who} handed this from ${names.person(before)} to ${holder.name}`;
        } else if (self) {
          handoff = `${holder.name} took it`;
        } else {
          handoff = `${who} assigned it to ${holder.name}`;
        }
        open(event, holder, firstName(holder.name), handoff);
        break;
      }
      case "unassigned":
        open(
          event,
          null,
          `${teamName} queue`,
          `${who} unassigned ${names.person(fromId(event.data, "assignee"))}`,
        );
        break;
      case "transferred": {
        const target = toId(event.data, "team");
        if (target) teamName = names.team(target);
        open(
          event,
          null,
          `${teamName} queue`,
          `${who} moved it from ${names.team(fromId(event.data, "team"))} to ${teamName}`,
        );
        break;
      }
      default:
        break;
    }
  }
  return collapse(segments);
}

/** More than six periods: keep the first two and the last three; fold the middle into "+N". */
function collapse(segments: Segment[]): Segment[] {
  if (segments.length <= MAX_SEGMENTS) return segments;
  const head = segments.slice(0, 2);
  const tail = segments.slice(-3);
  const middle = segments.slice(2, -3);
  const first = middle[0];
  const last = middle[middle.length - 1];
  if (!first || !last) return segments;
  const folded: Segment = {
    id: `fold-${first.id}`,
    holder: null,
    label: `+${String(middle.length)}`,
    start: first.start,
    end: last.end,
    current: false,
    handoff: `${String(middle.length)} earlier handoffs`,
    reason: null,
    collapsed: middle.length,
  };
  return [...head, folded, ...tail];
}

function toId(data: EventOut["data"], field: string): string | undefined {
  const value = data[field] as { to?: unknown } | undefined;
  return typeof value?.to === "string" ? value.to : undefined;
}

function fromId(data: EventOut["data"], field: string): string | undefined {
  const value = data[field] as { from?: unknown } | undefined;
  return typeof value?.from === "string" ? value.from : undefined;
}

/** Segment widths ∝ log(duration), with a floor so a 5-minute hold stays visible (DESIGN §5). */
export function segmentWeight(segment: Segment): number {
  if (segment.collapsed > 0) return 0.6;
  const minutes = Math.max(0, (segment.end - segment.start) / 60_000);
  return Math.max(1, Math.log(1 + minutes));
}

export function durationLabel(ms: number): string {
  const minutes = Math.max(0, Math.round(ms / 60_000));
  if (minutes < 1) return "under 1m";
  if (minutes < 60) return `${String(minutes)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) {
    const rest = minutes % 60;
    return rest > 0 ? `${String(hours)}h ${String(rest)}m` : `${String(hours)}h`;
  }
  const days = Math.floor(hours / 24);
  const restHours = hours % 24;
  return restHours > 0 ? `${String(days)}d ${String(restHours)}h` : `${String(days)}d`;
}
