/** Admin jobs (SPEC §9, §11): the outbox by status, and the one action on it, "retry a dead job". */
import { keepPreviousData, useInfiniteQuery, useQueryClient } from "@tanstack/react-query";

import type { components } from "../../api/generated";
import { ApiError, api, unwrap } from "../../lib/api";
import { useCommand } from "../../lib/useCommand";

export type Job = components["schemas"]["JobOut"];
export type JobStatus = components["schemas"]["OutboxStatus"];

export const JOB_TABS: { status: JobStatus; label: string }[] = [
  { status: "pending", label: "Pending" },
  { status: "dead", label: "Dead" },
  { status: "done", label: "Recently done" },
];

const PAGE = 50;

export const jobsKey = (status: JobStatus) => ["admin", "jobs", status] as const;

/** A keyset page at a time (I8); `next_cursor` is the id to continue from. */
export function useJobs(status: JobStatus, enabled: boolean) {
  return useInfiniteQuery({
    queryKey: jobsKey(status),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/admin/jobs", {
          params: { query: { status, limit: PAGE, ...(pageParam ? { cursor: pageParam } : {}) } },
        }),
      ),
    initialPageParam: null as number | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
    placeholderData: keepPreviousData,
    enabled,
    // The worker moves jobs between tabs on its own; keep the open tab honest.
    refetchInterval: 15_000,
  });
}

/** Retry is confirmed, not optimistic: the row shows a spinner until the server says it was requeued. */
export function useRetryJob() {
  const qc = useQueryClient();
  return useCommand<number, Job>({
    run: (id, ctx) =>
      unwrap(
        api.POST("/api/v1/admin/jobs/{job_id}/retry", {
          params: { path: { job_id: id }, header: { "Idempotency-Key": ctx.idempotencyKey } },
        }),
      ),
    onError: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
      return false; // the default toast explains it
    },
  });
}

export function isForbidden(error: unknown): error is ApiError {
  return error instanceof ApiError && error.status === 403;
}
