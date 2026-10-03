/** Server reads, as TanStack Query hooks. Writes go through `useCommand`. */
import { keepPreviousData, useInfiniteQuery, useQuery } from "@tanstack/react-query";

import { ApiError, api, unwrap } from "../lib/api";
import { type ListFilters, toFacetQuery, toListQuery } from "../lib/filters";
import { keys } from "./keys";

export const PAGE_SIZE = 50;

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

export function itemQuery(key: string) {
  return {
    queryKey: keys.item(key),
    queryFn: () => unwrap(api.GET("/api/v1/items/{key}", { params: { path: { key } } })),
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
