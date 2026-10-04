import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api";
import { type Job, type JobStatus } from "./jobs";
import { JobsPage } from "./JobsPage";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("@tanstack/react-query", async (original) => ({
  ...(await original<typeof import("@tanstack/react-query")>()),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}));

const state: { admin: boolean; rows: Job[]; error: unknown } = {
  admin: true,
  rows: [],
  error: null,
};

vi.mock("../../api/queries", () => ({
  useMe: () => ({ isPending: false, data: { user: { is_admin: state.admin } } }),
}));
vi.mock("./jobs", async (original) => ({
  ...(await original<typeof import("./jobs")>()),
  useJobs: () => ({
    isPending: false,
    isError: state.error !== null,
    error: state.error,
    data: { pages: [{ items: state.rows, next_cursor: null }] },
    hasNextPage: false,
    refetch: vi.fn(),
  }),
  useRetryJob: () => ({ execute: vi.fn(() => Promise.resolve({})) }),
}));

function job(id: number, status: JobStatus): Job {
  return {
    id,
    topic: "notify.email",
    status,
    attempts: status === "dead" ? 5 : 1,
    available_at: "2026-10-04T00:00:00Z",
    last_error: status === "dead" ? "SMTP timeout" : null,
    created_at: "2026-10-04T00:00:00Z",
    processed_at: null,
    payload: {},
  };
}

function renderAt(status: JobStatus, rows: Job[], admin = true, error: unknown = null) {
  state.admin = admin;
  state.rows = rows;
  state.error = error;
  render(
    <MemoryRouter initialEntries={[`/admin/jobs?status=${status}`]}>
      <JobsPage />
    </MemoryRouter>,
  );
}

describe("admin jobs", () => {
  it("dead jobs have a Retry button, one per row", () => {
    renderAt("dead", [job(1, "dead"), job(2, "dead")]);
    expect(screen.getAllByRole("button", { name: /^Retry job/ })).toHaveLength(2);
    expect(screen.getAllByText("SMTP timeout", { selector: "summary" })).toHaveLength(2);
  });

  it("pending jobs never show Retry", () => {
    renderAt("pending", [job(3, "pending")]);
    expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
  });

  it("recently done jobs never show Retry", () => {
    renderAt("done", [job(4, "done")]);
    expect(screen.queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
  });

  it("an empty Dead tab says the system is healthy", () => {
    renderAt("dead", []);
    expect(screen.getByText("No failed jobs.")).toBeInTheDocument();
    expect(screen.getByText("Notifications and checks are running normally.")).toBeInTheDocument();
  });

  it("a non-admin gets the forbidden state and no table", () => {
    renderAt("dead", [job(1, "dead")], false);
    expect(screen.getByText("Only admins can see jobs.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("a 403 from the server shows its reason", () => {
    const forbidden = new ApiError({
      status: 403,
      code: "FORBIDDEN",
      detail: "Only admins can view jobs.",
    });
    renderAt("dead", [], true, forbidden);
    expect(screen.getByText("Only admins can view jobs.")).toBeInTheDocument();
  });
});
