import { describe, expect, it } from "vitest";

import { emptyFilters, parseFilters, serializeFilters } from "./filters";

const roundTrip = (search: string) =>
  serializeFilters(parseFilters(new URLSearchParams(search))).toString();

describe("URL <-> filters", () => {
  it("round-trips a canonical URL unchanged", () => {
    const url =
      "team=PAY&status=new&status=in_progress&priority=0&priority=1&assignee=me&overdue=true&sort=due";
    expect(roundTrip(url)).toBe(url);
  });

  it("round-trips filters through the URL and back to equal filters", () => {
    const filters = {
      ...emptyFilters(),
      team: "SRE",
      status: ["blocked" as const, "awaiting_approval" as const],
      priority: [0, 2],
      type: ["incident" as const],
      requester: "me",
      q: "refund stuck",
      sort: "updated" as const,
    };
    expect(parseFilters(serializeFilters(filters))).toEqual(filters);
  });

  it("drops invalid values one by one instead of failing the whole page", () => {
    const parsed = parseFilters(
      new URLSearchParams(
        "team=TOOLONGKEY&status=open&status=blocked&priority=9&priority=1&assignee=bob&sort=weird&overdue=maybe",
      ),
    );
    expect(parsed).toEqual({ status: ["blocked"], priority: [1], type: [], sort: "priority" });
  });

  it("normalises team case, de-duplicates and orders repeated values", () => {
    expect(roundTrip("team=pay&priority=2&priority=0&priority=2&status=blocked&status=new")).toBe(
      "team=PAY&status=new&status=blocked&priority=0&priority=2",
    );
  });

  it("leaves the default sort out so links stay short", () => {
    expect(serializeFilters(emptyFilters()).toString()).toBe("");
  });
});
