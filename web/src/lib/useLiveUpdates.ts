/**
 * Live updates (SPEC §10, DESIGN §2.5). One EventSource on `/api/v1/stream` for the whole signed-in
 * app. The server only says "this item changed" (`{key, version, event_id}`); everything shown is
 * refetched through the normal authorized API and merged by (version, last_event_id), so a late or
 * duplicate nudge can never roll the screen back (I13).
 *
 *  - `item.changed` is coalesced per item over 250 ms: twenty events in a burst, one refetch.
 *  - `resync` (the server dropped our backlog, or could not replay) refetches everything on screen.
 *  - `notification.created` sets the bell's unread count straight from the event.
 *  - Reconnects back off (1 s, 2 s, 4 s ... 30 s) and send `last_event_id` so the server can replay.
 *  - After 3 failed connects in a row the hook polls every 10 s instead, and keeps trying to
 *    reconnect in the background; the first success stops the polling.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { keys } from "../api/keys";
import { UNREAD_CAP } from "./notifications";
import { compareVersion } from "./itemCache";
import type { ItemOut } from "./api";

export const POLL_INTERVAL_MS = 10_000;
export const COALESCE_MS = 250;
export const FAILED_CONNECTS_BEFORE_POLLING = 3;
const MAX_BACKOFF_MS = 30_000;
export const STREAM_URL = "/api/v1/stream";

type QueryClientLike = ReturnType<typeof useQueryClient>;

interface ItemChanged {
  key: string;
  version: number;
  event_id: number;
}

/** What is on screen: the rendered items and their timelines, the lists, the inbox. */
export function refreshOnScreen(qc: QueryClientLike): void {
  // `active` limits this to queries something is rendering; the rest refetch when next shown.
  void qc.invalidateQueries({ queryKey: ["item"], refetchType: "active" });
  void qc.invalidateQueries({ queryKey: keys.lists, refetchType: "active" });
  void qc.invalidateQueries({ queryKey: keys.attention, refetchType: "active" });
  void qc.invalidateQueries({ queryKey: keys.notifications, refetchType: "active" });
}

function alreadyHave(qc: QueryClientLike, change: ItemChanged): boolean {
  const cached = qc.getQueryData<ItemOut>(keys.item(change.key));
  return (
    cached !== undefined &&
    compareVersion(cached, { version: change.version, last_event_id: change.event_id }) >= 0
  );
}

/** Refetch what an item change touched. The item query merges, so this is safe at any time. */
export function applyItemChanges(qc: QueryClientLike, changes: Iterable<ItemChanged>): void {
  for (const change of changes) {
    // Our own mutation already put this version in the cache: nothing to fetch.
    if (alreadyHave(qc, change)) continue;
    void qc.invalidateQueries({ queryKey: keys.item(change.key), exact: true });
    void qc.invalidateQueries({ queryKey: keys.events(change.key) });
  }
  void qc.invalidateQueries({ queryKey: keys.lists, refetchType: "active" });
  void qc.invalidateQueries({ queryKey: keys.attention, refetchType: "active" });
}

function parse(event: Event): unknown {
  try {
    return JSON.parse((event as MessageEvent<string>).data);
  } catch {
    return null;
  }
}

export function useLiveUpdates({
  intervalMs = POLL_INTERVAL_MS,
  coalesceMs = COALESCE_MS,
}: { intervalMs?: number; coalesceMs?: number } = {}): void {
  const qc = useQueryClient();

  useEffect(() => {
    let source: EventSource | undefined;
    let closed = false;
    let failures = 0;
    let lastEventId: number | undefined;
    let reconnectTimer: number | undefined;
    let pollTimer: number | undefined;
    let flushTimer: number | undefined;
    const pending = new Map<string, ItemChanged>();

    const flush = () => {
      flushTimer = undefined;
      const batch = [...pending.values()];
      pending.clear();
      applyItemChanges(qc, batch);
    };

    const tick = () => {
      if (document.visibilityState === "visible") refreshOnScreen(qc);
    };
    const startPolling = () => {
      pollTimer ??= window.setInterval(tick, intervalMs);
    };
    const stopPolling = () => {
      window.clearInterval(pollTimer);
      pollTimer = undefined;
    };

    const connect = () => {
      if (closed) return;
      const url =
        lastEventId === undefined
          ? STREAM_URL
          : `${STREAM_URL}?last_event_id=${String(lastEventId)}`;
      const es = new EventSource(url);
      source = es;

      es.onopen = () => {
        // Back after polling or a gap: catch up now; the server replayed or sent `resync` anyway.
        if (failures > 0) refreshOnScreen(qc);
        failures = 0;
        stopPolling();
      };
      es.onerror = () => {
        es.close();
        if (closed) return;
        failures += 1;
        if (failures >= FAILED_CONNECTS_BEFORE_POLLING) startPolling();
        const backoff = Math.min(1_000 * 2 ** (failures - 1), MAX_BACKOFF_MS);
        reconnectTimer = window.setTimeout(connect, backoff);
      };

      es.addEventListener("item.changed", (event) => {
        const change = parse(event) as ItemChanged | null;
        if (!change) return;
        lastEventId = Math.max(lastEventId ?? 0, change.event_id);
        const queued = pending.get(change.key);
        if (!queued || queued.event_id < change.event_id) pending.set(change.key, change);
        flushTimer ??= window.setTimeout(flush, coalesceMs);
      });
      es.addEventListener("resync", () => {
        pending.clear();
        window.clearTimeout(flushTimer);
        flushTimer = undefined;
        refreshOnScreen(qc);
      });
      es.addEventListener("notification.created", (event) => {
        const body = parse(event) as { unread: number } | null;
        if (!body) return;
        qc.setQueryData(keys.unreadCount, Math.min(body.unread, UNREAD_CAP + 1));
        void qc.invalidateQueries({ queryKey: keys.notifications, exact: true });
      });
    };

    const onVisibility = () => {
      if (document.visibilityState === "visible") tick(); // catch up now, don't wait
    };

    connect();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      closed = true;
      source?.close();
      window.clearTimeout(reconnectTimer);
      window.clearTimeout(flushTimer);
      stopPolling();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [qc, intervalMs, coalesceMs]);
}
