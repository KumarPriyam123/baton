import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { TeamSettingsPage } from "./TeamSettingsPage";
import type { Member, TeamRole } from "./teamMembers";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const state: { myRole: TeamRole | null; admin: boolean } = { myRole: "member", admin: false };

const person = (id: string, name: string, role: TeamRole): Member => ({
  user: { id, name, email: `${id}@baton.test` },
  role,
});
const MEMBERS = [
  person("asha", "Asha Rao", "member"),
  person("priya", "Priya Nair", "lead"),
  person("dev", "Dev Malhotra", "viewer"),
];

vi.mock("../../api/queries", () => ({
  useMe: () => ({ isPending: false, data: { user: { is_admin: state.admin } } }),
  useTeams: () => ({
    isPending: false,
    isError: false,
    data: [{ key: "PAY", name: "Payments", my_role: state.myRole }],
  }),
}));
vi.mock("./teamMembers", async (original) => ({
  ...(await original<typeof import("./teamMembers")>()),
  useTeamMembers: () => ({
    isPending: false,
    isError: false,
    data: { pages: [{ items: MEMBERS, next_cursor: null }] },
    hasNextPage: false,
  }),
  useMemberCommand: () => ({ execute: vi.fn(() => Promise.resolve(true)) }),
  useDirectory: () => ({ data: { items: [] }, isPending: false }),
  useOwnedOpenItems: () => ({ count: 0, isPending: false, isError: false }),
}));

function renderPage(myRole: TeamRole | null, admin = false) {
  state.myRole = myRole;
  state.admin = admin;
  render(
    <MemoryRouter initialEntries={["/teams/PAY/settings"]}>
      <Routes>
        <Route path="/teams/:key/settings" element={<TeamSettingsPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("team settings controls follow the viewer's role", () => {
  it("a plain member sees the list and no controls", () => {
    renderPage("member");
    expect(screen.getByText("Asha Rao")).toBeInTheDocument();
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    expect(screen.queryByText(/^Add to/)).not.toBeInTheDocument();
  });

  it("a viewer sees no controls either", () => {
    renderPage("viewer");
    expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
  });

  it("a lead can change and remove members and viewers, and add people", () => {
    renderPage("lead");
    expect(screen.getByRole("combobox", { name: "Role of Asha Rao" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Dev Malhotra" })).toBeInTheDocument();
    expect(screen.getByText("Add to Payments")).toBeInTheDocument();
  });

  it("a lead cannot touch another lead", () => {
    renderPage("lead");
    expect(screen.queryByRole("combobox", { name: "Role of Priya Nair" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove Priya Nair" })).not.toBeInTheDocument();
  });

  it("an admin outside the team can manage leads too, and may hand out the lead role", () => {
    renderPage(null, true);
    expect(screen.getByRole("combobox", { name: "Role of Priya Nair" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Remove Priya Nair" })).toBeInTheDocument();
    const options = screen
      .getByRole("combobox", { name: "Role of Asha Rao" })
      .querySelectorAll("option");
    expect([...options].map((o) => o.value)).toEqual(["viewer", "member", "lead"]);
  });

  it("a lead is not offered the lead role", () => {
    renderPage("lead");
    const options = screen
      .getByRole("combobox", { name: "Role of Asha Rao" })
      .querySelectorAll("option");
    expect([...options].map((o) => o.value)).toEqual(["viewer", "member"]);
  });
});
