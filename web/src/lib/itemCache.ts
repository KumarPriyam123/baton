/**
 * Keeping one truth for each item across the detail cache, every list and the inbox (I13, SPEC §6.6).
 *
 * The rule: an incoming item replaces the cached one only if (version, last_event_id) is not older.
 * Comments don't bump `version`, so `last_event_id` breaks the tie. A slow response that arrives
 * after a newer one therefore can't roll the screen back.
 */
import type { InfiniteData, QueryClient } from "@tanstack/react-query";

import type { components } from "../api/generated";
import { keys } from "../api/keys";
import type { ItemOut } from "./api";

type ItemsPage = components["schemas"]["ItemsPage"];
type AttentionOut = components["schemas"]["AttentionOut"];

export type Versioned = Pick<ItemOut, "version" | "last_event_id">;

/** Negative when `a` is older than `b`, zero when equal, positive when newer. */
export function compareVersion(a: Versioned, b: Versioned): number {
  if (a.version !== b.version) return a.version - b.version;
  return (a.last_event_id ?? 0) - (b.last_event_id ?? 0);
}

/** The newer of the two. Equal wins for `incoming`: same version, fresher derived fields. */
export function mergeItem<T extends Versioned>(cached: T | undefined, incoming: T): T {
  if (cached === undefined) return incoming;
  return compareVersion(incoming, cached) >= 0 ? incoming : cached;
}

function mapInfinite(
  data: InfiniteData<ItemsPage> | undefined,
  fn: (items: ItemOut[]) => ItemOut[],
): InfiniteData<ItemsPage> | undefined {
  if (!data) return data;
  return { ...data, pages: data.pages.map((page) => ({ ...page, items: fn(page.items) })) };
}

/** Writes `incoming` into the detail cache and every list and attention section holding the item. */
export function upsertItem(qc: QueryClient, incoming: ItemOut): void {
  qc.setQueryData<ItemOut>(keys.item(incoming.key), (cached) => mergeItem(cached, incoming));

  const replace = (items: ItemOut[]): ItemOut[] =>
    items.map((item) => (item.id === incoming.id ? mergeItem(item, incoming) : item));

  qc.setQueriesData<InfiniteData<ItemsPage>>({ queryKey: keys.lists }, (data) =>
    mapInfinite(data, replace),
  );
  qc.setQueriesData<AttentionOut>({ queryKey: keys.attention }, (data) =>
    data ? { sections: data.sections.map((s) => ({ ...s, items: replace(s.items) })) } : data,
  );
}

/** The server said 404: gone or no longer visible. Drop it everywhere (DESIGN §6.2). */
export function removeItem(qc: QueryClient, ref: { id: string; key: string }): void {
  qc.removeQueries({ queryKey: keys.item(ref.key) });
  const drop = (items: ItemOut[]): ItemOut[] => items.filter((item) => item.id !== ref.id);
  qc.setQueriesData<InfiniteData<ItemsPage>>({ queryKey: keys.lists }, (data) =>
    mapInfinite(data, drop),
  );
  qc.setQueriesData<AttentionOut>({ queryKey: keys.attention }, (data) =>
    data
      ? {
          sections: data.sections.map((s) => {
            const items = drop(s.items);
            return { ...s, items, count: Math.max(0, s.count - (s.items.length - items.length)) };
          }),
        }
      : data,
  );
}

export function getCachedItem(qc: QueryClient, key: string): ItemOut | undefined {
  return qc.getQueryData<ItemOut>(keys.item(key));
}
