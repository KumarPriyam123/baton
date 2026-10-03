import { X } from "lucide-react";

/** A removable filter: `Team: Payments ✕`. The remove button names what it removes. */
export function FilterToken({ label, onRemove }: { label: string; onRemove: () => void }) {
  return (
    <span className="inline-flex h-7 items-center gap-1 rounded-chip border border-rule bg-sheet pr-1 pl-2 text-meta text-ink">
      {label}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`Remove filter ${label}`}
        className="inline-flex size-5 cursor-pointer items-center justify-center rounded-chip text-pencil hover:bg-dispatch-wash"
      >
        <X className="size-3.5" strokeWidth={1.75} />
      </button>
    </span>
  );
}

export function Kbd({ children }: { children: string }) {
  return (
    <kbd className="inline-flex h-5 min-w-5 items-center justify-center rounded-chip border border-rule px-1 text-small text-pencil">
      {children}
    </kbd>
  );
}
