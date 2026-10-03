import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, renderHook, waitFor } from "@testing-library/react";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";

import { keys } from "../api/keys";
import { makeItem } from "../test/factories";
import { ApiError, type ItemOut, NetworkError } from "./api";
import { type CommandContext, useCommand } from "./useCommand";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

type Run = (input: string, ctx: CommandContext) => Promise<ItemOut>;

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const item = makeItem({ version: 7 });
  qc.setQueryData(keys.item(item.key), item);
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return { qc, item, wrapper };
}

const busy = () => new ApiError({ status: 503, code: "BUSY", detail: "Busy, try again" });

describe("useCommand", () => {
  it("reuses one Idempotency-Key and the same If-Match across every retry of one action", async () => {
    const { item, wrapper } = setup();
    const seen: CommandContext[] = [];
    const run = vi
      .fn<Run>()
      .mockImplementationOnce((_, ctx) => {
        seen.push(ctx);
        return Promise.reject(new NetworkError(new Error("response dropped")));
      })
      .mockImplementationOnce((_, ctx) => {
        seen.push(ctx);
        return Promise.reject(busy());
      })
      .mockImplementationOnce((_, ctx) => {
        seen.push(ctx);
        return Promise.resolve(makeItem({ version: 8 }));
      });

    const { result } = renderHook(
      () => useCommand({ item, needsIfMatch: true, run, retryDelay: () => 0 }),
      { wrapper },
    );
    await act(() => result.current.execute("go"));

    expect(run).toHaveBeenCalledTimes(3);
    expect(new Set(seen.map((c) => c.idempotencyKey)).size).toBe(1);
    expect(seen.map((c) => c.ifMatch)).toEqual(['"7"', '"7"', '"7"']);
  });

  it("uses a new key for the next user action", async () => {
    const { item, wrapper } = setup();
    const keysSeen: string[] = [];
    const run = vi.fn<Run>((_, ctx) => {
      keysSeen.push(ctx.idempotencyKey);
      return Promise.resolve(makeItem({ version: 7 }));
    });
    const { result } = renderHook(() => useCommand({ item, run }), { wrapper });
    await act(() => result.current.execute("a"));
    await act(() => result.current.execute("b"));
    expect(keysSeen[0]).not.toBe(keysSeen[1]);
  });

  it("uses the key the caller passes, so repeat submits of one form are one action", async () => {
    const { item, wrapper } = setup();
    const keysSeen: string[] = [];
    const run = vi.fn<Run>((_, ctx) => {
      keysSeen.push(ctx.idempotencyKey);
      return Promise.resolve(makeItem({ version: 7 }));
    });
    const { result } = renderHook(() => useCommand({ item, run }), { wrapper });
    await act(() => result.current.execute("a", { idempotencyKey: "form-key" }));
    await act(() => result.current.execute("a", { idempotencyKey: "form-key" }));
    expect(keysSeen).toEqual(["form-key", "form-key"]);
  });

  it("does not retry a 4xx: the answer would be the same", async () => {
    const { item, wrapper } = setup();
    const run = vi
      .fn<Run>()
      .mockRejectedValue(
        new ApiError({ status: 409, code: "ALREADY_CLAIMED", detail: "Asha took it" }),
      );
    const { result } = renderHook(() => useCommand({ item, run, retryDelay: () => 0 }), {
      wrapper,
    });
    await act(async () => {
      await result.current.execute("x").catch(() => undefined);
    });
    expect(run).toHaveBeenCalledTimes(1);
  });

  it("gives up after three retries", async () => {
    const { item, wrapper } = setup();
    const run = vi.fn<Run>().mockRejectedValue(busy());
    const { result } = renderHook(() => useCommand({ item, run, retryDelay: () => 0 }), {
      wrapper,
    });
    await act(async () => {
      await result.current.execute("x").catch(() => undefined);
    });
    expect(run).toHaveBeenCalledTimes(4); // first try + 3 retries
  });

  it("runs two commands on one item in order, the second with the version the first returned", async () => {
    const { item, wrapper } = setup();
    const order: string[] = [];
    const ifMatches: (string | undefined)[] = [];
    const run = vi.fn<Run>(async (name, ctx) => {
      order.push(`start ${name}`);
      ifMatches.push(ctx.ifMatch);
      await new Promise((resolve) => setTimeout(resolve, 10));
      order.push(`end ${name}`);
      return makeItem({ version: name === "first" ? 8 : 9 });
    });
    const { result } = renderHook(() => useCommand({ item, needsIfMatch: true, run }), {
      wrapper,
    });

    await act(async () => {
      await Promise.all([result.current.execute("first"), result.current.execute("second")]);
    });
    await waitFor(() => {
      expect(order).toEqual(["start first", "end first", "start second", "end second"]);
    });
    expect(ifMatches).toEqual(['"7"', '"8"']);
  });
});
