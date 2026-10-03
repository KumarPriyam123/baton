import { cn } from "./cn";

/** Static class names: Tailwind only emits classes it can see written out in full. */
const HUES = [
  "bg-av-0",
  "bg-av-1",
  "bg-av-2",
  "bg-av-3",
  "bg-av-4",
  "bg-av-5",
  "bg-av-6",
  "bg-av-7",
] as const;

export function hueFor(id: string): number {
  let hash = 0;
  for (const char of id) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % HUES.length;
}

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  const first = parts[0]?.[0] ?? "?";
  const last = parts.length > 1 ? (parts[parts.length - 1]?.[0] ?? "") : "";
  return (first + last).toUpperCase();
}

/** Initials on one of eight muted hues chosen from the user id. No person: dashed empty circle. */
export function Avatar({
  person,
  size = 24,
  className,
}: {
  person: { id: string; name: string } | null;
  size?: number;
  className?: string;
}) {
  const style = { width: size, height: size, fontSize: Math.round(size * 0.42) };
  if (!person) {
    return (
      <span
        role="img"
        aria-label="No owner"
        style={style}
        className={cn(
          "inline-block shrink-0 rounded-full border border-dashed border-pencil",
          className,
        )}
      />
    );
  }
  return (
    <span
      role="img"
      aria-label={person.name}
      title={person.name}
      style={style}
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-strong text-on-hue",
        HUES[hueFor(person.id)],
        className,
      )}
    >
      {initials(person.name)}
    </span>
  );
}
