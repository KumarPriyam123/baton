/**
 * ConflictDialog choices produce the right request (BUILD_PLAN phase 10, T-RECONCILE):
 * the first save carries the version the editor started from; "Save my version" resends on the
 * server's new version with a new Idempotency-Key; "Discard" sends nothing; disjoint fields rebase
 * by themselves.
 */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { keys } from "../../api/keys";
import { ApiError } from "../../lib/api";
import { makeItem } from "../../test/factories";
import { Description } from "./Description";

interface Init {
  params: { header: Record<string, string> };
  body: Record<string, unknown>;
}
const patch = vi.fn<(path: string, init: Init) => Promise<unknown>>();
vi.mock("../../lib/api", async (original) => ({
  ...(await original<typeof import("../../lib/api")>()),
  api: { PATCH: (path: string, init: Init) => patch(path, init) },
}));
const toast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn() }));
vi.mock("sonner", () => ({ toast }));

const mine = "Original text, with my new sentence.";

function setup() {
  const item = makeItem({
    version: 7,
    description: "Original text.",
    allowed_actions: ["comment", "edit_text"],
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  qc.setQueryData(keys.item(item.key), item);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  render(<Description item={item} />, { wrapper });
}

function editAndSave() {
  fireEvent.click(screen.getByRole("button", { name: /Edit/ }));
  fireEvent.change(screen.getByRole("textbox", { name: "Description" }), {
    target: { value: mine },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save description" }));
}

const theirs = makeItem({ version: 8, description: "Their rewrite.", last_event_id: 120 });

function conflict(changes: Record<string, unknown>[]) {
  return new ApiError({
    status: 412,
    code: "VERSION_CONFLICT",
    detail: "changed",
    current: theirs,
    changesSince: changes,
  });
}
const theirDescriptionEdit = {
  kind: "field_changed",
  actor: { name: "Asha Rao" },
  data: { description: { from: "Original text.", to: "Their rewrite." } },
};
const theirPriorityEdit = {
  kind: "priority_changed",
  actor: { name: "Asha Rao" },
  data: { priority: { from: 2, to: 1 } },
};

describe("description editor and 412", () => {
  beforeEach(() => {
    window.localStorage.clear();
    patch.mockReset();
    toast.error.mockReset();
    toast.success.mockReset();
  });

  it("same field changed: opens the conflict dialog with a diff, after sending the editor's own version", async () => {
    patch.mockRejectedValueOnce(conflict([theirDescriptionEdit]));
    setup();
    editAndSave();

    const dialog = await screen.findByRole("dialog");
    expect(
      within(dialog).getByText("This request changed while you were editing"),
    ).toBeInTheDocument();
    expect(within(dialog).getByTestId("diff")).toBeInTheDocument();
    expect(patch).toHaveBeenCalledTimes(1);
    expect(patch.mock.calls[0]?.[1].params.header["If-Match"]).toBe('"7"');
  });

  it("[Save my version] sends the draft on the server's NEW version with a new Idempotency-Key", async () => {
    patch
      .mockRejectedValueOnce(conflict([theirDescriptionEdit]))
      .mockResolvedValueOnce({ data: makeItem({ version: 9, description: mine }) });
    setup();
    editAndSave();
    fireEvent.click(await screen.findByRole("button", { name: "Save my version" }));

    await waitFor(() => {
      expect(patch).toHaveBeenCalledTimes(2);
    });
    const [first, second] = patch.mock.calls.map((c) => c[1]);
    expect(second?.params.header["If-Match"]).toBe('"8"');
    expect(second?.body.description).toBe(mine);
    expect(second?.params.header["Idempotency-Key"]).not.toBe(
      first?.params.header["Idempotency-Key"],
    );
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Saved");
    });
  });

  it("[Discard my draft] sends nothing and closes the editor", async () => {
    patch.mockRejectedValueOnce(conflict([theirDescriptionEdit]));
    setup();
    editAndSave();
    fireEvent.click(await screen.findByRole("button", { name: "Discard my draft" }));

    expect(patch).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
    expect(screen.queryByRole("textbox", { name: "Description" })).not.toBeInTheDocument();
  });

  it("different field changed: saves by itself on the new version and says what was kept", async () => {
    patch
      .mockRejectedValueOnce(conflict([theirPriorityEdit]))
      .mockResolvedValueOnce({ data: makeItem({ version: 9, description: mine }) });
    setup();
    editAndSave();

    await waitFor(() => {
      expect(patch).toHaveBeenCalledTimes(2);
    });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(patch.mock.calls[1]?.[1].params.header["If-Match"]).toBe('"8"');
    await waitFor(() => {
      expect(toast.success).toHaveBeenCalledWith("Saved. Asha Rao's change to priority was kept.");
    });
  });
});
