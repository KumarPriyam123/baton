/** Server reads, as TanStack Query hooks. Writes go through `useCommand`. */
import {
  type QueryClient,
  keepPreviousData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiError, type ItemOut, api, unwrap } from "../lib/api";
import type { components } from "./generated";
import { type ListFilters, toFacetQuery, toListQuery } from "../lib/filters";
import { mergeItem } from "../lib/itemCache";
import { UNREAD_CAP } from "../lib/notifications";
import { keys } from "./keys";

export const PAGE_SIZE = 50;

export type DecisionKind = components["schemas"]["EventKind"];

export function useMe() {
  return useQuery({
    queryKey: keys.me,
    queryFn: () => unwrap(api.GET("/api/v1/me")),
    staleTime: 5 * 60_000,
  });
}

export function useTeams() {
  return useQuery({
    queryKey: keys.teams,
    queryFn: () => unwrap(api.GET("/api/v1/teams")),
    staleTime: 5 * 60_000,
  });
}

export function useAttention() {
  return useQuery({
    queryKey: keys.attention,
    queryFn: () => unwrap(api.GET("/api/v1/me/attention")),
  });
}

/** The dashboard (SPEC 7): one aggregate per team, refreshed every 30 s while it is on screen. */
export function useTeamStats() {
  return useQuery({
    queryKey: keys.stats,
    queryFn: () => unwrap(api.GET("/api/v1/stats/teams")),
    refetchInterval: 30_000,
  });
}

/** The decision log, newest first, a keyset page at a time (I8). Empty strings mean "any". */
export function useDecisions(team: string, kind: string) {
  return useInfiniteQuery({
    queryKey: keys.decisions(team, kind),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/decisions", {
          params: {
            query: {
              limit: PAGE_SIZE,
              ...(team ? { team } : {}),
              ...(kind ? { kind: [kind as DecisionKind] } : {}),
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
}

/** How many are unread: one more than the cap is fetched, so "20+" is known without counting. */
export function useUnreadCount() {
  return useQuery({
    queryKey: keys.unreadCount,
    queryFn: async () => {
      const page = await unwrap(
        api.GET("/api/v1/me/notifications", {
          params: { query: { unread: true, limit: UNREAD_CAP + 1 } },
        }),
      );
      return page.items.length;
    },
    refetchInterval: 20_000,
  });
}

/** The popover's list: the latest 20, read and unread, fetched only while it is open. */
export function useNotifications(enabled: boolean) {
  return useQuery({
    queryKey: keys.notifications,
    queryFn: () =>
      unwrap(api.GET("/api/v1/me/notifications", { params: { query: { limit: 20 } } })),
    enabled,
    refetchInterval: enabled ? 20_000 : false,
  });
}

/** Marks some (`ids`) or all notifications read, then refreshes both notification queries. */
export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { ids: number[]; all: false } | { all: true }) =>
      unwrap(api.POST("/api/v1/me/notifications/read", { body })),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.notifications }),
  });
}

/**
 * A refetch can arrive after a newer mutation response. The query function merges with what is
 * cached, so an older answer never replaces a newer one (I13, SPEC 6.6 version guard).
 */
export function itemQuery(key: string) {
  return {
    queryKey: keys.item(key),
    queryFn: async ({ client }: { client: QueryClient }) => {
      const fetched = await unwrap(api.GET("/api/v1/items/{key}", { params: { path: { key } } }));
      return mergeItem(client.getQueryData<ItemOut>(keys.item(key)), fetched);
    },
  };
}

export function useItem(key: string | undefined) {
  return useQuery({
    ...itemQuery(key ?? ""),
    enabled: key !== undefined,
  });
}

/**
 * Keyset-paged list. With `q` the server returns the best 50 and no cursor, so the same hook
 * serves search. The previous results stay on screen while a new filter loads (no flash).
 */
export function useItemList(filters: ListFilters) {
  return useInfiniteQuery({
    queryKey: keys.list(filters),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/items", {
          params: {
            query: {
              ...toListQuery(filters),
              limit: PAGE_SIZE,
              ...(pageParam ? { cursor: pageParam } : {}),
            },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
  });
}

export function useFacets(filters: ListFilters) {
  return useQuery({
    queryKey: keys.facets(filters),
    queryFn: () =>
      unwrap(api.GET("/api/v1/items/facets", { params: { query: toFacetQuery(filters) } })),
    placeholderData: keepPreviousData,
    staleTime: 15_000,
  });
}

/** Newest first, a page at a time (I8); the timeline reverses them and offers "Show earlier". */
export function useEvents(key: string | undefined) {
  return useInfiniteQuery({
    queryKey: keys.events(key ?? ""),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/items/{key}/events", {
          params: {
            path: { key: key ?? "" },
            query: { order: "desc", limit: 100, after_event_id: pageParam },
          },
        }),
      ),
    initialPageParam: 0,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    enabled: key !== undefined,
  });
}

/** The first page of a team's members: names for ids in events, and the assignee picker. */
export function useMembers(teamKey: string | undefined) {
  return useQuery({
    queryKey: keys.members(teamKey ?? ""),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/teams/{key}/members", {
          params: { path: { key: teamKey ?? "" }, query: { limit: 50 } },
        }),
      ),
    enabled: teamKey !== undefined,
    staleTime: 60_000,
  });
}

export function useSimilar(teamId: string | undefined, title: string) {
  const trimmed = title.trim();
  return useQuery({
    queryKey: keys.similar(teamId ?? "", trimmed),
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/items/similar", {
          params: { query: { team_id: teamId ?? "", title: trimmed } },
        }),
      ),
    enabled: teamId !== undefined && trimmed.length >= 4,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

/** Queries retry network errors and 5xx twice with backoff, and never 4xx (phase 8). */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof ApiError) return error.status >= 500;
  return true; // network error
}
