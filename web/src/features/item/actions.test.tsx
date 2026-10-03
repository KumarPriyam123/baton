import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { components } from "../../api/generated";
import { makeItem } from "../../test/factories";
import { ACTIONS, barModel, pickPrimary } from "./actions";
import { ActionBar } from "./ActionBar";
import type { ItemActions } from "./useItemActions";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("../../api/queries", () => ({ useTeams: () => ({ data: [] }) }));

type Action = components["schemas"]["Action"];

const idle: ItemActions = {
  run: vi.fn(() => Promise.resolve(true)),
  pending: null,
  stale: null,
  dismissStale: vi.fn(),
  needsApproval: false,
};

function renderBar(allowed: Action[], nextKind = "in_progress") {
  const item = makeItem({
    allowed_actions: allowed,
    next_step: { kind: nextKind, label: "x", severity: "low" },
  });
  return render(<ActionBar item={item} actions={idle} members={[]} />);
}

describe("action bar is driven by allowed_actions", () => {
  it("unowned item with claim allowed: primary is Assign to me", () => {
    renderBar(["comment", "watch", "claim", "close"], "needs_owner");
    expect(screen.getByRole("button", { name: "Assign to me" })).toBeInTheDocument();
  });

  it("no claim in allowed_actions: no Assign to me, whatever the next step says", () => {
    renderBar(["comment", "watch"], "needs_owner");
    expect(screen.queryByRole("button", { name: "Assign to me" })).not.toBeInTheDocument();
    expect(screen.getByTestId("no-actions")).toBeInTheDocument();
  });

  it("awaiting your approval: Approve is primary and Reject sits beside it", () => {
    renderBar(["comment", "approve", "reject", "cancel_approval"], "approval");
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /More/ })).toBeInTheDocument(); // cancel_approval
  });

  it("without approve in allowed_actions there is no Approve button", () => {
    renderBar(["comment", "cancel_approval"], "approval");
    expect(screen.queryByRole("button", { name: "Approve" })).not.toBeInTheDocument();
  });

  it("comment, watch and field edits are not buttons in the bar", () => {
    renderBar(["comment", "watch", "edit_text", "change_priority"]);
    expect(screen.queryByRole("group", { name: "Actions" })).not.toBeInTheDocument();
  });

  it("the model never offers an action the server did not allow (every pair of actions)", () => {
    const all = Object.keys(ACTIONS) as Action[];
    for (const a of all) {
      for (const b of all) {
        const allowed: Action[] = [a, b];
        for (const kind of ["needs_owner", "approval", "resolved", "blocked", "in_progress"]) {
          const model = barModel(allowed, { kind });
          const shown = [model.primary, ...model.beside, ...model.more]
            .filter((s) => s !== undefined)
            .map((s) => s.action);
          for (const action of shown) expect(allowed).toContain(action);
          expect(pickPrimary([], { kind })).toBeUndefined();
        }
      }
    }
  });
});
