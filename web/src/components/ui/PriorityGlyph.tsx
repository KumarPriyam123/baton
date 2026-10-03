import { cn } from "./cn";

const LABEL = ["P0 urgent", "P1 high", "P2 normal", "P3 low"] as const;
const TONE = ["text-p0", "text-p1-text", "text-p2", "text-p3"] as const;

/**
 * Four bars, filled from the left: P0 all four … P3 one (DESIGN §2.6). Always with its word, so
 * the colour is never the only signal.
 */
export function PriorityGlyph({ priority, className }: { priority: number; className?: string }) {
  const filled = 4 - priority;
  return (
    <span className={cn("inline-flex items-center gap-2", TONE[priority], className)}>
      <span className="flex h-4 items-end gap-0.5" aria-hidden>
        {[0, 1, 2, 3].map((i) => (
          <span
            key={i}
            className={cn("w-1 rounded-[1px] bg-current", i >= filled && "opacity-25")}
            style={{ height: 6 + i * 3 }}
          />
        ))}
      </span>
      <span className="text-body text-ink">{LABEL[priority] ?? `P${String(priority)}`}</span>
    </span>
  );
}
