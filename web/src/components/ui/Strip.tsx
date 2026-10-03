/**
 * The strip (DESIGN §4.1): one line, a priority edge, the next step in words.
 *   ▌ PAY-142  ◐ Refund stuck for order 48213     Needs your approval   (AR)   2h  •
 * Status is shape + word (icon with an accessible label); colour is never the only signal.
 */
import {
  CircleCheck,
  CircleDashed,
  CircleDot,
  CircleSlash,
  CircleX,
  Hourglass,
  Lock,
  type LucideIcon,
} from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import type { ItemOut } from "../../lib/api";
import { STATUS_LABEL, ageShort, fullTime } from "../../lib/format";
import { Avatar } from "./Avatar";
import { cn } from "./cn";

const STATUS_ICON: Record<ItemOut["status"], LucideIcon> = {
  new: CircleDashed,
  in_progress: CircleDot,
  blocked: CircleSlash,
  awaiting_approval: Hourglass,
  resolved: CircleCheck,
  closed: CircleX,
};

/** P0 red, P1 amber, P2 steel; P3 has no edge. Static strings so Tailwind emits them. */
const EDGE = ["bg-p0", "bg-p1", "bg-p2", "bg-transparent"] as const;

const CHIP = {
  high: "bg-p0-wash text-p0",
  medium: "bg-p1-wash text-p1-text",
  low: "text-pencil",
} as const;

export function StatusIcon({
  status,
  className,
}: {
  status: ItemOut["status"];
  className?: string;
}) {
  const Icon = STATUS_ICON[status];
  return (
    <Icon
      role="img"
      aria-label={STATUS_LABEL[status]}
      className={cn("size-4 shrink-0", status === "closed" && "opacity-60", className)}
      strokeWidth={1.75}
    />
  );
}

/** Marks the words of `query` inside `text` (case-insensitive). No HTML is ever injected. */
function Highlighted({ text, query }: { text: string; query: string | undefined }) {
  const words = (query ?? "").split(/\s+/).filter((w) => w.length >= 2);
  if (words.length === 0) return <>{text}</>;
  const escaped = words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  return (
    <>
      {text.split(pattern).map((part, i) =>
        i % 2 === 1 ? (
          <mark key={i} className="rounded-chip bg-dispatch-wash px-0.5 text-ink">
            {part}
          </mark>
        ) : (
          part
        ),
      )}
    </>
  );
}

export function NextStepChip({ step }: { step: ItemOut["next_step"] }) {
  return (
    <span
      className={cn(
        "inline-flex max-w-64 items-center truncate rounded-chip px-2 py-0.5 text-small font-strong",
        CHIP[step.severity],
      )}
    >
      <span className="truncate">{step.label}</span>
    </span>
  );
}

export function Strip({
  item,
  selected = false,
  to,
  onPrefetch,
  actions,
  now,
  highlight,
}: {
  item: ItemOut;
  selected?: boolean;
  to: string;
  onPrefetch?: (item: ItemOut) => void;
  /** Hover-revealed quick actions on the right. */
  actions?: ReactNode;
  now?: number;
  /** Search text: matching words in the title are marked. */
  highlight?: string | undefined;
}) {
  const unread = item.unread_since_event_id !== null;
  return (
    <div
      role="listitem"
      className={cn(
        "group relative flex h-11 items-stretch border-b border-rule transition-colors duration-100 ease-linear",
        selected ? "bg-dispatch-wash" : "hover:bg-dispatch-wash/60",
      )}
    >
      <span aria-hidden className={cn("w-1 shrink-0", EDGE[item.priority])} />
      <Link
        to={to}
        data-strip
        data-key={item.key}
        aria-current={selected ? "true" : undefined}
        onFocus={() => onPrefetch?.(item)}
        onMouseEnter={() => onPrefetch?.(item)}
        className="flex min-w-0 flex-1 items-center gap-3 pr-4 pl-3 text-ink no-underline focus-visible:outline-offset-[-2px]"
      >
        <span className="tnum w-16 shrink-0 text-meta text-pencil">{item.key}</span>
        <StatusIcon status={item.status} />
        <span className="min-w-0 flex-1 truncate text-body font-strong">
          <Highlighted text={item.title} query={highlight} />
          {item.confidential && (
            <Lock
              role="img"
              aria-label="Confidential"
              className="ml-2 inline size-3.5 align-text-bottom text-pencil"
              strokeWidth={1.75}
            />
          )}
        </span>
        <NextStepChip step={item.next_step} />
        <Avatar person={item.assignee} />
        <span
          className="tnum w-9 shrink-0 text-right text-small text-pencil"
          title={`Updated ${fullTime(item.updated_at)}`}
        >
          {ageShort(item.updated_at, now)}
        </span>
        <span
          className={cn("size-2 shrink-0 rounded-full", unread ? "bg-dispatch" : "bg-transparent")}
          role={unread ? "img" : undefined}
          aria-label={unread ? "Updated since you last opened it" : undefined}
          aria-hidden={unread ? undefined : true}
        />
      </Link>
      {actions && (
        <div className="absolute inset-y-0 right-10 hidden items-center bg-inherit pl-2 group-focus-within:flex group-hover:flex">
          {actions}
        </div>
      )}
    </div>
  );
}
