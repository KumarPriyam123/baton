import { describe, expect, it } from "vitest";

import { UNREAD_CAP, unreadBadge } from "./notifications";

describe("unread badge", () => {
  it("shows nothing when there is nothing unread, or the count is not known yet", () => {
    expect(unreadBadge(0)).toBeNull();
    expect(unreadBadge(undefined)).toBeNull();
  });

  it("shows the exact number up to and including the cap", () => {
    expect(unreadBadge(1)).toBe("1");
    expect(unreadBadge(UNREAD_CAP)).toBe(String(UNREAD_CAP));
  });

  it("caps the number: one more than the cap reads as 'cap+', however many are unread", () => {
    expect(unreadBadge(UNREAD_CAP + 1)).toBe(`${String(UNREAD_CAP)}+`);
    expect(unreadBadge(10_000)).toBe(`${String(UNREAD_CAP)}+`);
  });
});
