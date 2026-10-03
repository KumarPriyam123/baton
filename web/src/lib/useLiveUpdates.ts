/**
 * Live updates (SPEC §10, DESIGN §2.5). Phase 7 (SSE) is not built, so this hook polls: every
 * 10 s it refetches the open item and its timeline, and the visible lists. It stops while the tab
 * is hidden and catches up the moment it is shown again.
 *
 * Refetched items go through `itemQuery`, whose function merges by (version, last_event_id), so a
 * slow poll can never roll the screen back (I13). The interface is what the SSE version needs too:
 * `{ itemKey }` in, caches kept fresh, nothing returned. SSE replaces the body, not the callers.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { keys } from "../api/keys";

export const POLL_INTERVAL_MS = 10_000;

export function refreshOnScreen(qc: ReturnType<typeof useQueryClient>, itemKey?: string): void {
  if (itemKey) {
    void qc.invalidateQueries({ queryKey: keys.item(itemKey), exact: true });
    void qc.invalidateQueries({ queryKey: keys.events(itemKey) });
  }
  // `active` limits this to queries something is rendering; the rest refetch when next shown.
  void qc.invalidateQueries({ queryKey: keys.lists, refetchType: "active" });
  void qc.invalidateQueries({ queryKey: keys.attention, refetchType: "active" });
}

export function useLiveUpdates({
  itemKey,
  intervalMs = POLL_INTERVAL_MS,
}: {
  itemKey?: string | undefined;
  intervalMs?: number;
}): void {
  const qc = useQueryClient();

  useEffect(() => {
    let timer: number | undefined;

    const tick = () => {
      if (document.visibilityState === "visible") refreshOnScreen(qc, itemKey);
    };
    const start = () => {
      timer ??= window.setInterval(tick, intervalMs);
    };
    const stop = () => {
      window.clearInterval(timer);
      timer = undefined;
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") {
        tick(); // catch up now, don't wait for the next interval
        start();
      } else {
        stop();
      }
    };

    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [qc, itemKey, intervalMs]);
}
