import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { keys } from "../api/keys";
import { makeItem } from "../test/factories";
import { POLL_INTERVAL_MS, useLiveUpdates } from "./useLiveUpdates";

class FakeEventSource {
  static all: FakeEventSource[] = [];
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private handlers = new Map<string, ((event: Event) => void)[]>();
  constructor(readonly url: string) {
    FakeEventSource.all.push(this);
  }
  addEventListener(name: string, handler: (event: Event) => void) {
    this.handlers.set(name, [...(this.handlers.get(name) ?? []), handler]);
  }
  close() {
    this.closed = true;
  }
  emit(name: string, data: unknown) {
    const event = new MessageEvent(name, { data: JSON.stringify(data) });
    for (const handler of this.handlers.get(name) ?? []) handler(event);
  }
  static latest(): FakeEventSource {
    const last = FakeEventSource.all[FakeEventSource.all.length - 1];
    if (!last) throw new Error("no EventSource was opened");
    return last;
  }
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, wrapper };
}

/** The live hook plus an observer rendering item `key`, counting how often it is fetched. */
function mountWithItem(key: string, fetches: () => void) {
  const { qc, wrapper } = setup();
  const item = makeItem({ key, version: 1 });
  renderHook(
    () => {
      useLiveUpdates();
      return useQuery({
        queryKey: keys.item(key),
        queryFn: () => {
          fetches();
          return Promise.resolve(item);
        },
        staleTime: Infinity,
      });
    },
    { wrapper },
  );
  return { qc, item };
}

describe("useLiveUpdates", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeEventSource.all = [];
    vi.stubGlobal("EventSource", FakeEventSource);
  });
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("twenty item.changed events for one item in 100 ms cause exactly one refetch", async () => {
    const fetches = vi.fn();
    mountWithItem("PAY-1", fetches);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetches).toHaveBeenCalledTimes(1); // the first render's fetch

    const stream = FakeEventSource.latest();
    for (let i = 0; i < 20; i++) {
      stream.emit("item.changed", { key: "PAY-1", version: 2, event_id: 100 + i });
      await act(async () => {
        await vi.advanceTimersByTimeAsync(5);
      });
    }
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetches).toHaveBeenCalledTimes(2); // not 21
  });

  it("does not refetch an item whose cached version is already as new as the event", async () => {
    const fetches = vi.fn();
    const { qc, item } = mountWithItem("PAY-1", fetches);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    qc.setQueryData(keys.item("PAY-1"), { ...item, version: 3, last_event_id: 500 });

    FakeEventSource.latest().emit("item.changed", { key: "PAY-1", version: 3, event_id: 500 });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });

    expect(fetches).toHaveBeenCalledTimes(1); // our own mutation's echo costs nothing
  });

  it("refetches different items once each", async () => {
    const a = vi.fn();
    mountWithItem("PAY-1", a);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const stream = FakeEventSource.latest();
    stream.emit("item.changed", { key: "PAY-1", version: 2, event_id: 10 });
    stream.emit("item.changed", { key: "PAY-2", version: 2, event_id: 11 }); // not on screen
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    expect(a).toHaveBeenCalledTimes(2);
  });

  it("a resync refetches what is on screen at once", async () => {
    const fetches = vi.fn();
    mountWithItem("PAY-1", fetches);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    FakeEventSource.latest().emit("resync", {});
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetches).toHaveBeenCalledTimes(2);
  });

  it("a notification event sets the bell's unread count", () => {
    const { qc, wrapper } = setup();
    renderHook(
      () => {
        useLiveUpdates();
      },
      { wrapper },
    );
    FakeEventSource.latest().emit("notification.created", { unread: 4 });
    expect(qc.getQueryData(keys.unreadCount)).toBe(4);
  });

  it("reconnects with backoff and sends the last event id it saw", async () => {
    const { wrapper } = setup();
    renderHook(
      () => {
        useLiveUpdates();
      },
      { wrapper },
    );
    const first = FakeEventSource.latest();
    first.emit("item.changed", { key: "PAY-1", version: 2, event_id: 42 });
    first.onerror?.();
    expect(first.closed).toBe(true);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    expect(FakeEventSource.all).toHaveLength(2);
    expect(FakeEventSource.latest().url).toBe("/api/v1/stream?last_event_id=42");
  });

  it("falls back to polling after three failed connects and stops once connected again", async () => {
    const fetches = vi.fn();
    mountWithItem("PAY-1", fetches);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetches).toHaveBeenCalledTimes(1);

    FakeEventSource.latest().onerror?.(); // failure 1, retry in 1 s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1_000);
    });
    FakeEventSource.latest().onerror?.(); // failure 2, retry in 2 s
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2_000);
    });
    expect(fetches).toHaveBeenCalledTimes(1); // still no polling after two
    FakeEventSource.latest().onerror?.(); // failure 3: polling starts
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS);
    });
    expect(fetches).toHaveBeenCalledTimes(2);

    FakeEventSource.latest().onopen?.(); // the stream is back (this also catches up once)
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    const afterReconnect = fetches.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(POLL_INTERVAL_MS * 3);
    });
    expect(fetches).toHaveBeenCalledTimes(afterReconnect); // polling stopped
  });

  it("closes the stream and its timers on unmount", () => {
    const { wrapper } = setup();
    const { unmount } = renderHook(
      () => {
        useLiveUpdates();
      },
      { wrapper },
    );
    const stream = FakeEventSource.latest();
    unmount();
    expect(stream.closed).toBe(true);
  });
});
