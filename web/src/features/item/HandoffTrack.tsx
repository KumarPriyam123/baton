/**
 * The handoff track (DESIGN §5): who has it now, who had it before, and for how long.
 * Each segment is a period of ownership; unowned periods are dashed; the current holder is thick
 * and in --dispatch. Width follows log(duration) with a floor. The same facts are available as a
 * visually hidden ordered list.
 */
import { useState } from "react";

import * as Tooltip from "@radix-ui/react-tooltip";

import { Avatar } from "../../components/ui/Avatar";
import { cn } from "../../components/ui/cn";
import { fullTime } from "../../lib/format";
import { type Segment, durationLabel, segmentWeight } from "./track";

function clock(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });
}

function rangeText(segment: Segment, ended: boolean): string {
  const end = segment.current && !ended ? "now" : clock(segment.end);
  return `${clock(segment.start)} → ${end}`;
}

export function HandoffTrack({
  segments,
  ended,
  itemKey,
  ready,
}: {
  segments: readonly Segment[];
  /** The item is resolved or closed: the last period has an end, not "now". */
  ended: boolean;
  itemKey: string;
  /** The events have loaded, so what is on screen now is "already there", not new. */
  ready: boolean;
}) {
  // Segments that appear after the first complete render grow in; the ones present at load do
  // not (§2.5: no entrance animation on page load). State adjusted during render, not in an effect.
  const [known, setKnown] = useState<{ key: string; ids: ReadonlySet<string> } | null>(null);
  if (ready && known?.key !== itemKey) {
    setKnown({ key: itemKey, ids: new Set(segments.map((s) => s.id)) });
  }
  const fresh = new Set(
    known?.key === itemKey ? segments.filter((s) => !known.ids.has(s.id)).map((s) => s.id) : [],
  );

  return (
    <section aria-label="Handoff track" className="mt-5">
      <Tooltip.Provider delayDuration={150}>
        <div className="flex items-stretch overflow-x-auto pb-1" data-testid="handoff-track">
          {segments.map((segment) => (
            <SegmentView
              key={segment.id}
              segment={segment}
              ended={ended}
              grow={fresh.has(segment.id)}
            />
          ))}
        </div>
      </Tooltip.Provider>
      <ol className="sr-only-live" aria-label="Ownership history">
        {segments.map((segment) => (
          <li key={segment.id}>
            {segment.collapsed > 0
              ? `${String(segment.collapsed)} earlier handoffs.`
              : `${segment.handoff} at ${fullTime(new Date(segment.start).toISOString())}. ${
                  segment.holder ? `Held by ${segment.holder.name}` : "Not owned"
                } for ${durationLabel(segment.end - segment.start)}${
                  segment.current && !ended ? ", and still current" : ""
                }.${segment.reason ? ` Reason: ${segment.reason}.` : ""}`}
          </li>
        ))}
      </ol>
    </section>
  );
}

function SegmentView({
  segment,
  ended,
  grow,
}: {
  segment: Segment;
  ended: boolean;
  grow: boolean;
}) {
  const live = segment.current && !ended;
  const owned = segment.holder !== null;
  const weight = segmentWeight(segment);
  const body = (
    <div className={cn("flex w-full min-w-0 flex-col gap-1 pr-2", grow && "grow-in")}>
      <div className="flex items-center gap-1.5">
        {owned ? (
          <Avatar
            person={segment.holder}
            size={live ? 24 : 20}
            className={cn(live && "ring-2 ring-dispatch ring-offset-2 ring-offset-sheet")}
          />
        ) : (
          <span
            aria-hidden
            className="size-2.5 shrink-0 rounded-full border border-pencil bg-sheet"
          />
        )}
        <span
          className={cn(
            "truncate text-meta",
            live ? "font-strong text-ink" : "text-pencil",
            segment.collapsed > 0 && "tnum",
          )}
        >
          {segment.label}
          {live && owned && " (now)"}
        </span>
      </div>
      <div
        aria-hidden
        className={cn(
          "w-full rounded-full",
          !owned && "h-0 border-t-2 border-dashed border-pencil",
          owned && !live && "h-0.5 bg-pencil",
          owned && live && "h-1 bg-dispatch",
        )}
      />
      {segment.collapsed === 0 && (
        <div className="tnum text-small text-pencil">
          <div className="truncate">{rangeText(segment, ended)}</div>
          <div>{durationLabel(segment.end - segment.start)}</div>
        </div>
      )}
    </div>
  );

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        {/* Focusable so the handoff text is reachable without a mouse; the sr-only list covers readers. */}
        <div
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          className="flex min-w-[96px] rounded-chip"
          style={{ flex: `${String(weight)} 1 0` }}
        >
          {body}
        </div>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content
          sideOffset={6}
          className="float-in z-50 max-w-72 rounded-panel border border-rule bg-sheet px-3 py-2 text-meta shadow-float"
        >
          <p>
            {segment.handoff}
            {segment.collapsed === 0 && ` at ${clock(segment.start)}`}
          </p>
          {segment.reason && <p className="mt-1 text-pencil">“{segment.reason}”</p>}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}
