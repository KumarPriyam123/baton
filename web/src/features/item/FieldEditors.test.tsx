import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { components } from "../../api/generated";
import { makeItem } from "../../test/factories";
import { DueEditor, PropertyChoice, TitleEditor } from "./FieldEditors";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));
vi.mock("./useSaveFields", () => ({
  useSaveFields: () => ({ save: vi.fn(), isPending: false }),
}));

type Action = components["schemas"]["Action"];
const itemWith = (allowed: Action[]) => makeItem({ allowed_actions: allowed });

describe("inline property editors appear only when the server allows the edit", () => {
  it("title: pencil with edit_text, plain heading without", () => {
    const { unmount } = render(<TitleEditor item={itemWith(["edit_text"])} washed={false} />);
    expect(screen.getByRole("button", { name: "Edit title" })).toBeInTheDocument();
    unmount();
    render(<TitleEditor item={itemWith(["comment"])} washed={false} />);
    expect(screen.queryByRole("button", { name: "Edit title" })).not.toBeInTheDocument();
    expect(
      screen.getByRole("heading", { name: "Refund stuck for order 48213" }),
    ).toBeInTheDocument();
  });

  it("due date: pencil with edit_due_at only", () => {
    const { unmount } = render(
      <DueEditor item={itemWith(["edit_due_at"])}>
        <span>No due date</span>
      </DueEditor>,
    );
    expect(screen.getByRole("button", { name: "Change due date" })).toBeInTheDocument();
    unmount();
    render(
      <DueEditor item={itemWith([])}>
        <span>No due date</span>
      </DueEditor>,
    );
    expect(screen.queryByRole("button", { name: "Change due date" })).not.toBeInTheDocument();
  });

  const yesNo = [
    { value: "yes", label: "Yes" },
    { value: "no", label: "No" },
  ];
  const choice = (item: ReturnType<typeof makeItem>, allowedValue: (v: string) => boolean) => (
    <PropertyChoice
      item={item}
      field="requires_approval"
      ariaLabel="Needs approval, change"
      current="yes"
      allowed={allowedValue}
      options={yesNo}
      fields={(v) => ({ requires_approval: v === "yes" })}
    >
      Yes
    </PropertyChoice>
  );

  it("a choice with no other value the server allows is plain text", () => {
    render(choice(itemWith([]), () => false));
    expect(
      screen.queryByRole("button", { name: "Needs approval, change" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("Yes")).toBeInTheDocument();
  });

  it("a choice with another allowed value is a menu button", () => {
    render(choice(itemWith(["requires_approval_off"]), (v) => v === "no"));
    expect(screen.getByRole("button", { name: "Needs approval, change" })).toBeInTheDocument();
  });
});
