import { type InfiniteData, QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import type { components } from "../api/generated";
import { keys } from "../api/keys";
import { makeItem } from "../test/factories";
import { emptyFilters } from "./filters";
import { compareVersion, mergeItem, removeItem, upsertItem } from "./itemCache";

type ItemsPage = components["schemas"]["ItemsPage"];
type AttentionOut = components["schemas"]["AttentionOut"];

describe("mergeItem", () => {
  it("ignores an older version so a slow response cannot roll the screen back", () => {
    const cached = makeItem({ version: 8, title: "newer" });
    const stale = makeItem({ version: 7, title: "older" });
    expect(mergeItem(cached, stale)).toBe(cached);
  });

  it("takes a newer version", () => {
    const incoming = makeItem({ version: 9 });
    expect(mergeItem(makeItem({ version: 8 }), incoming)).toBe(incoming);
  });

  it("breaks a version tie with last_event_id, because comments do not bump version", () => {
    const cached = makeItem({ version: 7, last_event_id: 120 });
    expect(mergeItem(cached, makeItem({ version: 7, last_event_id: 110 }))).toBe(cached);
    const newer = makeItem({ version: 7, last_event_id: 130 });
    expect(mergeItem(cached, newer)).toBe(newer);
  });

  it("lets an equal (version, last_event_id) replace, so derived fields like next_step refresh", () => {
    const incoming = makeItem({ title: "same version, fresher labels" });
    expect(mergeItem(makeItem(), incoming)).toBe(incoming);
  });

  it("compares version before last_event_id", () => {
    expect(
      compareVersion({ version: 8, last_event_id: 1 }, { version: 7, last_event_id: 999 }),
    ).toBeGreaterThan(0);
  });

  it("accepts anything when nothing is cached", () => {
    const incoming = makeItem();
    expect(mergeItem(undefined, incoming)).toBe(incoming);
  });
});

function seededClient(item = makeItem()) {
  const qc = new QueryClient();
  const page: InfiniteData<ItemsPage> = {
    pages: [{ items: [item, makeItem({ id: "other", key: "PAY-7" })], next_cursor: null }],
    pageParams: [null],
  };
  qc.setQueryData(keys.item(item.key), item);
  qc.setQueryData(keys.list(emptyFilters()), page);
  qc.setQueryData<AttentionOut>(keys.attention, {
    sections: [{ key: "urgent", title: "Yours, overdue or urgent", count: 1, items: [item] }],
  });
  return qc;
}

describe("upsertItem", () => {
  it("updates the detail cache, every list and the inbox in one call", () => {
    const qc = seededClient();
    upsertItem(qc, makeItem({ version: 8, title: "Renamed" }));

    expect(qc.getQueryData<{ title: string }>(keys.item("PAY-142"))?.title).toBe("Renamed");
    const list = qc.getQueryData<InfiniteData<ItemsPage>>(keys.list(emptyFilters()));
    expect(list?.pages[0]?.items[0]?.title).toBe("Renamed");
    expect(list?.pages[0]?.items[1]?.key).toBe("PAY-7"); // untouched
    const attention = qc.getQueryData<AttentionOut>(keys.attention);
    expect(attention?.sections[0]?.items[0]?.title).toBe("Renamed");
  });

  it("does not let an older copy overwrite a newer one in any cache", () => {
    const qc = seededClient(makeItem({ version: 9, title: "newest" }));
    upsertItem(qc, makeItem({ version: 8, title: "stale" }));
    const list = qc.getQueryData<InfiniteData<ItemsPage>>(keys.list(emptyFilters()));
    expect(list?.pages[0]?.items[0]?.title).toBe("newest");
    expect(qc.getQueryData<{ title: string }>(keys.item("PAY-142"))?.title).toBe("newest");
  });
});

describe("removeItem", () => {
  it("drops the item from the detail cache, lists and inbox after a 404", () => {
    const item = makeItem();
    const qc = seededClient(item);
    removeItem(qc, { id: item.id, key: item.key });

    expect(qc.getQueryData(keys.item("PAY-142"))).toBeUndefined();
    const list = qc.getQueryData<InfiniteData<ItemsPage>>(keys.list(emptyFilters()));
    expect(list?.pages[0]?.items.map((i) => i.key)).toEqual(["PAY-7"]);
    const attention = qc.getQueryData<AttentionOut>(keys.attention);
    expect(attention?.sections[0]?.items).toEqual([]);
    expect(attention?.sections[0]?.count).toBe(0);
  });
});
