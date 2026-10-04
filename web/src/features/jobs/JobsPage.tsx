/**
 * Jobs (DESIGN §4.9, admins only): the outbox by status. Pending · Dead · Recently done; dead rows
 * have Retry. Non-admins get a designed 403 (the server's reason) or 404 state, never a blank page.
 */
import { useQueryClient } from "@tanstack/react-query";
import { formatDistanceToNowStrict } from "date-fns";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";

import { useMe } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { ApiError } from "../../lib/api";
import { fullTime } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { JOB_TABS, type Job, type JobStatus, isForbidden, useJobs, useRetryJob } from "./jobs";

const EMPTY: Record<JobStatus, { title: string; body: string }> = {
  pending: { title: "Nothing is waiting.", body: "Every queued notification and check has run." },
  dead: {
    title: "No failed jobs.",
    body: "Notifications and checks are running normally.",
  },
  done: { title: "Nothing has run recently.", body: "Finished jobs show up here." },
};

function nextAttempt(job: Job, now: number): string {
  if (job.status === "done") return "Done";
  if (job.status === "dead") return "Won't run again";
  const when = new Date(job.available_at).getTime();
  return when <= now ? "Due now" : `in ${formatDistanceToNowStrict(when)}`;
}

export function JobsPage() {
  const me = useMe();
  const [params, setParams] = useSearchParams();
  const raw = params.get("status");
  const status: JobStatus = JOB_TABS.some((t) => t.status === raw) ? (raw as JobStatus) : "pending";
  const isAdmin = me.data?.user.is_admin ?? false;
  const jobs = useJobs(status, isAdmin);

  if (me.isPending) return <JobsSkeleton />;
  if (!isAdmin || isForbidden(jobs.error)) {
    return (
      <EmptyState
        title="Only admins can see jobs."
        action={
          <Link to="/inbox" className="text-body text-dispatch">
            Go to your inbox
          </Link>
        }
      >
        {jobs.error instanceof ApiError && jobs.error.status === 403
          ? jobs.error.detail
          : "Background jobs are visible to administrators."}
      </EmptyState>
    );
  }
  if (jobs.error instanceof ApiError && jobs.error.status === 404) {
    return (
      <EmptyState
        title="This page doesn't exist."
        action={
          <Link to="/inbox" className="text-body text-dispatch">
            Go to your inbox
          </Link>
        }
      >
        Check the address, or use the rail to find what you need.
      </EmptyState>
    );
  }

  const rows = jobs.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <header className="px-6 pb-4 pt-6">
        <h1 className="text-title">Jobs</h1>
        <p className="text-meta text-pencil">
          Notifications and checks the worker runs in the background.
        </p>
      </header>

      <div role="tablist" aria-label="Job status" className="flex gap-1 border-b border-rule px-6">
        {JOB_TABS.map((tab) => (
          <button
            key={tab.status}
            type="button"
            role="tab"
            id={`jobs-tab-${tab.status}`}
            aria-selected={tab.status === status}
            aria-controls="jobs-panel"
            onClick={() => {
              setParams(tab.status === "pending" ? {} : { status: tab.status }, { replace: true });
            }}
            className={cn(
              "-mb-px h-10 cursor-pointer border-b-2 px-3 text-body text-pencil hover:text-ink",
              tab.status === status ? "border-dispatch font-strong text-ink" : "border-transparent",
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div id="jobs-panel" role="tabpanel" aria-labelledby={`jobs-tab-${status}`}>
        {jobs.isPending ? (
          <JobsSkeleton />
        ) : jobs.isError ? (
          <ErrorState
            title="Couldn't load jobs."
            error={jobs.error}
            onRetry={() => void jobs.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState title={EMPTY[status].title}>{EMPTY[status].body}</EmptyState>
        ) : (
          <>
            <JobsTable rows={rows} status={status} />
            {jobs.hasNextPage && (
              <div className="px-6 py-4">
                <Button
                  variant="secondary"
                  pending={jobs.isFetchingNextPage}
                  onClick={() => void jobs.fetchNextPage()}
                >
                  Show more
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function JobsTable({ rows, status }: { rows: Job[]; status: JobStatus }) {
  const now = useNow();
  const qc = useQueryClient();
  const retry = useRetryJob();
  // The job being retried, so its button spins and the others stay usable.
  const [retrying, setRetrying] = useState<number | null>(null);

  const retryJob = async (id: number) => {
    setRetrying(id);
    try {
      await retry.execute(id);
      void qc.invalidateQueries({ queryKey: ["admin", "jobs"] });
      toast.success("Job requeued");
    } catch {
      // useCommand toasted the reason
    } finally {
      setRetrying(null);
    }
  };

  return (
    <table className="w-full border-collapse text-body">
      <thead>
        <tr className="border-b border-rule text-left text-meta text-pencil">
          <th scope="col" className="px-6 py-2 font-strong">
            Topic
          </th>
          <th scope="col" className="w-24 px-3 py-2 text-right font-strong">
            Attempts
          </th>
          <th scope="col" className="px-3 py-2 font-strong">
            Last error
          </th>
          <th scope="col" className="w-40 px-3 py-2 font-strong">
            Next attempt
          </th>
          {status === "dead" && (
            <th scope="col" className="w-28 px-6 py-2">
              <span className="sr-only-live">Actions</span>
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((job) => (
          <tr key={job.id} className="border-b border-rule align-top">
            <td className="px-6 py-3">
              <span className="font-strong">{job.topic}</span>
              <span className="tnum ml-2 text-meta text-pencil">#{job.id}</span>
            </td>
            <td className="tnum px-3 py-3 text-right">{job.attempts}</td>
            <td className="max-w-0 px-3 py-3">
              {job.last_error ? (
                <details>
                  <summary className="cursor-pointer truncate text-p0">{job.last_error}</summary>
                  <pre className="mt-2 max-h-48 overflow-auto whitespace-pre-wrap text-meta text-ink">
                    {job.last_error}
                  </pre>
                </details>
              ) : (
                <span className="text-pencil">None</span>
              )}
            </td>
            <td className="tnum px-3 py-3" title={fullTime(job.available_at)}>
              {nextAttempt(job, now)}
            </td>
            {status === "dead" && (
              <td className="px-6 py-2 text-right">
                <Button
                  variant="secondary"
                  pending={retrying === job.id}
                  disabled={retrying !== null && retrying !== job.id}
                  aria-label={`Retry job ${job.topic} #${String(job.id)}`}
                  onClick={() => void retryJob(job.id)}
                >
                  Retry
                </Button>
              </td>
            )}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function JobsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading jobs" className="space-y-3 px-6 py-4">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  );
}
