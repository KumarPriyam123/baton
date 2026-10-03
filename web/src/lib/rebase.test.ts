import { describe, expect, it, vi } from "vitest";

import { makeItem } from "../test/factories";
import { ApiError } from "./api";
import { describeKept, planRebase, runWithRebase } from "./rebase";

const priorityByAsha = {
  kind: "priority_changed",
  actor: { name: "Asha Rao" },
  data: { priority: { from: 2, to: 1 }, due_at: { from: null, to: "2026-10-05T00:00:00Z" } },
};
const descriptionByAsha = {
  kind: "field_changed",
  actor: { name: "Asha Rao" },
  data: { description: { from: "a", to: "b" } },
};

function conflict(changes: unknown[] | undefined, version = 8) {
  return new ApiError({
    status: 412,
    code: "VERSION_CONFLICT",
    detail: "changed",
    current: makeItem({ version }),
    changesSince: changes as Record<string, unknown>[] | undefined,
  });
}

describe("planRebase", () => {
  it("resends when the fields I changed and the fields they changed are disjoint", () => {
    const plan = planRebase(["description"], [priorityByAsha]);
    expect(plan.kind).toBe("resend");
    expect(plan.theirFields).toEqual(["priority", "due_at"]);
    expect(plan.authors).toEqual(["Asha Rao"]);
  });

  it("is a conflict when the same field changed on both sides", () => {
    const plan = planRebase(["description"], [priorityByAsha, descriptionByAsha]);
    expect(plan).toMatchObject({ kind: "conflict", overlap: ["description"] });
  });

  it("is a conflict when the server did not say what changed (cannot prove disjoint)", () => {
    expect(planRebase(["title"], undefined).kind).toBe("conflict");
  });

  it("ignores events that carry no field map (approval invalidation lists)", () => {
    const plan = planRebase(
      ["description"],
      [{ kind: "approval_invalidated", data: { fields: ["description"] } }],
    );
    expect(plan.kind).toBe("resend");
  });
});

describe("runWithRebase", () => {
  it("disjoint fields: sends again on the NEW version and reports what was kept", async () => {
    const send = vi
      .fn<(v: number) => Promise<string>>()
      .mockRejectedValueOnce(conflict([priorityByAsha], 8))
      .mockResolvedValueOnce("saved");
    const outcome = await runWithRebase(["description"], 7, send);
    expect(send.mock.calls.map((c) => c[0])).toEqual([7, 8]);
    expect(outcome).toMatchObject({ kind: "saved", result: "saved" });
    expect(outcome.kind === "saved" && outcome.kept && describeKept(outcome.kept)).toBe(
      "Saved. Asha Rao's change to priority and due date was kept.",
    );
  });

  it("overlapping fields: does not resend, hands back the conflict", async () => {
    const send = vi
      .fn<(v: number) => Promise<string>>()
      .mockRejectedValue(conflict([descriptionByAsha], 8));
    const outcome = await runWithRebase(["description"], 7, send);
    expect(send).toHaveBeenCalledTimes(1);
    expect(outcome.kind).toBe("conflict");
  });

  it("gives up after the resend limit instead of looping", async () => {
    const send = vi
      .fn<(v: number) => Promise<string>>()
      .mockImplementation((v) => Promise.reject(conflict([priorityByAsha], v + 1)));
    const outcome = await runWithRebase(["description"], 1, send, 2);
    expect(send).toHaveBeenCalledTimes(3);
    expect(outcome.kind).toBe("conflict");
  });

  it("lets every other error through untouched", async () => {
    const forbidden = new ApiError({ status: 403, code: "FORBIDDEN", detail: "no" });
    await expect(runWithRebase(["title"], 1, () => Promise.reject(forbidden))).rejects.toBe(
      forbidden,
    );
  });
});
