/** The unread badge on the bell (DESIGN §4.11): a small number, never an unbounded one. */
export const UNREAD_CAP = 20;

/**
 * `count` is how many unread notifications the server returned for a page of `UNREAD_CAP + 1`,
 * so more than the cap means "more than the cap". Nothing unread shows no badge at all.
 */
export function unreadBadge(count: number | undefined): string | null {
  if (!count || count < 1) return null;
  return count > UNREAD_CAP ? `${String(UNREAD_CAP)}+` : String(count);
}
