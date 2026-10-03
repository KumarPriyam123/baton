/**
 * Word-level diff (jsdiff). Removed words are struck through, added words underlined, so the
 * change reads without colour (DESIGN §2.6: colour is never the only signal).
 */
import { diffWords } from "diff";

import { cn } from "./cn";

export function DiffView({
  before,
  after,
  className,
}: {
  before: string;
  after: string;
  className?: string;
}) {
  const parts = diffWords(before, after);
  return (
    <div
      className={cn(
        "max-h-64 overflow-y-auto rounded-chip border border-rule bg-desk p-3 text-body whitespace-pre-wrap",
        className,
      )}
      data-testid="diff"
    >
      {parts.map((part, i) =>
        part.added ? (
          <ins key={i} className="bg-dispatch-wash text-ink underline decoration-dispatch">
            {part.value}
          </ins>
        ) : part.removed ? (
          <del key={i} className="bg-p0-wash text-p0 line-through">
            {part.value}
          </del>
        ) : (
          <span key={i}>{part.value}</span>
        ),
      )}
    </div>
  );
}
