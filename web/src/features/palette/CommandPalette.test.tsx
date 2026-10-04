import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeAll, describe, expect, it, vi } from "vitest";

import type { components } from "../../api/generated";
import { makeItem } from "../../test/factories";
import { CommandPalette } from "./CommandPalette";
import { paletteActionSpecs, priorityChoices } from "./model";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type Action = components["schemas"]["Action"];
const state: { allowed: Action[]; admin: boolean } = { allowed: [], admin: false };

vi.mock("../../api/queries", () => ({
  useItem: (key: string | undefined) => ({
    data: key
      ? makeItem({
          allowed_actions: state.allowed,
          next_step: { kind: "progress", label: "x", severity: "low" },
        })
      : undefined,
  }),
  useMe: () => ({
    data: { user: { id: "u1", name: "Priya", email: "p@x", is_admin: state.admin } },
  }),
  useMembers: () => ({ data: { items: [] } }),
  useTeams: () => ({ data: [{ key: "PAY", name: "Payments", my_role: "lead" }] }),
  PAGE_SIZE: 50,
}));
vi.mock("../../app/shell/Shell", () => ({ useShell: () => ({ openNewRequest: vi.fn() }) }));

beforeAll(() => {
  // cmdk measures and scrolls; jsdom has neither.
  globalThis.ResizeObserver = class {
    observe = vi.fn();
    unobserve = vi.fn();
    disconnect = vi.fn();
  };
  Element.prototype.scrollIntoView = vi.fn();
});

async function openPalette(path: string, allowed: Action[], admin = false) {
  state.allowed = allowed;
  state.admin = admin;
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <CommandPalette />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.keyDown(document, { key: "k", ctrlKey: true });
  return { dialog: await screen.findByRole("dialog") };
}

describe("palette actions follow allowed_actions", () => {
  it("offers exactly the allowed workflow actions, and nothing the server left out", async () => {
    const { dialog } = await openPalette("/items/PAY-142", ["comment", "claim", "block", "watch"]);
    const actions = within(dialog).getByRole("group", { name: "Actions on PAY-142" });
    expect(within(actions).getByText("Assign to me")).toBeInTheDocument();
    expect(within(actions).getByText("Block")).toBeInTheDocument();
    for (const missing of ["Resolve", "Approve", "Reject", "Unassign", "Request approval"]) {
      expect(within(actions).queryByText(missing)).not.toBeInTheDocument();
    }
    // No change_priority in the list: no "Set priority" entries either.
    expect(within(actions).queryByText(/Set priority/)).not.toBeInTheDocument();
  });

  it("with no item-level actions allowed there is no actions group at all", async () => {
    const { dialog } = await openPalette("/items/PAY-142", ["comment"]);
    expect(within(dialog).queryByText(/Actions on/)).not.toBeInTheDocument();
  });

  it("away from an item there are no item actions, only navigation", async () => {
    const { dialog } = await openPalette("/inbox", ["claim", "resolve"]);
    expect(within(dialog).queryByText(/Actions on/)).not.toBeInTheDocument();
    expect(within(dialog).getByText("Dashboard")).toBeInTheDocument();
  });

  it("Jobs is offered to admins only", async () => {
    const plain = await openPalette("/inbox", []);
    expect(within(plain.dialog).queryByText("Jobs")).not.toBeInTheDocument();
  });

  it("Jobs is offered to admins", async () => {
    const { dialog } = await openPalette("/inbox", [], true);
    expect(within(dialog).getByText("Jobs")).toBeInTheDocument();
  });

  it("typing narrows the actions", async () => {
    const { dialog } = await openPalette("/items/PAY-142", ["claim", "block", "resolve"]);
    fireEvent.change(within(dialog).getByRole("combobox"), { target: { value: "blo" } });
    const actions = within(dialog).getByRole("group", { name: "Actions on PAY-142" });
    expect(within(actions).getByText("Block")).toBeInTheDocument();
    expect(within(actions).queryByText("Resolve")).not.toBeInTheDocument();
  });

  it("Esc closes it", async () => {
    await openPalette("/inbox", []);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});

describe("paletteActionSpecs / priorityChoices", () => {
  it("never returns an action that is not in allowed_actions", () => {
    const all: Action[] = ["claim", "release", "block", "unblock", "resolve", "close", "transfer"];
    for (const only of all) {
      const item = makeItem({ allowed_actions: [only] });
      for (const spec of paletteActionSpecs(item)) expect(spec.action).toBe(only);
    }
  });

  it("leaves out close and transfer, whose forms live in the action bar", () => {
    const item = makeItem({ allowed_actions: ["close", "transfer", "resolve"] });
    expect(paletteActionSpecs(item).map((s) => s.action)).toEqual(["resolve"]);
  });

  it("offers every priority but the current one, only with change_priority", () => {
    expect(priorityChoices(makeItem({ priority: 1, allowed_actions: [] }))).toEqual([]);
    const labels = priorityChoices(makeItem({ priority: 1, allowed_actions: ["change_priority"] }));
    expect(labels.map((c) => c.label)).toEqual([
      "Set priority P0",
      "Set priority P2",
      "Set priority P3",
    ]);
  });
});
