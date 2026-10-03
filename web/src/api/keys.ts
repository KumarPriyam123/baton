/**
 * Query keys, in one place so invalidation and cache merging agree (I13).
 * Every list-shaped cache sits under ["items", "list"] so `upsertItem` can find all of them.
 */
import type { ListFilters } from "../lib/filters";

export const keys = {
  me: ["me"] as const,
  teams: ["teams"] as const,
  attention: ["attention"] as const,
  item: (key: string) => ["item", key] as const,
  lists: ["items", "list"] as const,
  list: (filters: ListFilters) => ["items", "list", filters] as const,
  facets: (filters: ListFilters) => ["items", "facets", filters] as const,
  similar: (teamId: string, title: string) => ["items", "similar", teamId, title] as const,
};
