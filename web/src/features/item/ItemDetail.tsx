/**
 * Item detail (DESIGN §4.4): header, handoff track, action bar, approval, properties, description,
 * timeline. The right-hand pane of the queue and the /items/:key page are this one component.
 *
 * It decides nothing about the workflow: buttons come from `allowed_actions` (I4), the primary one
 * from `next_step`, every number from the server's item. Live changes arrive over the
 * app-wide stream (lib/useLiveUpdates.ts, mounted in the shell) and are merged, never overwritten.
 */
import { useQueryClient } from "@tanstack/react-query";
import { Lock, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router";

import { keys } from "../../api/keys";
import { useEvents, useItem, useMe } from "../../api/queries";
import { Avatar } from "../../components/ui/Avatar";
import { PriorityGlyph } from "../../components/ui/PriorityGlyph";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { NextStepChip, StatusIcon } from "../../components/ui/Strip";
import { announce } from "../../lib/announce";
import { ApiError, type ItemOut, api, unwrap } from "../../lib/api";
import { type EventOut, lastEventSentence } from "../../lib/eventText";
import { STATUS_LABEL, TYPE_LABEL } from "../../lib/format";
import { compareVersion } from "../../lib/itemCache";
import { useCommand } from "../../lib/useCommand";
import { useNow } from "../../lib/useNow";
import { ActionBar } from "./ActionBar";
import { ApprovalPanel, StaleApprovalPanel } from "./ApprovalPanel";
import { Description } from "./Description";
import { TitleEditor } from "./FieldEditors";
import { HandoffTrack } from "./HandoffTrack";
import { useNames } from "./names";
import { Properties } from "./Properties";
import { Timeline, type TimelineHandle } from "./Timeline";
import { buildTrack } from "./track";
import { useItemActions } from "./useItemActions";

const MAX_EVENT_PAGES = 5; // 500 events: more than any real item, and a bound (I8)
const WATCHED_FIELDS = ["status", "priority", "type", "title", "description", "due_at"] as const;

/** Which visible fields differ between two versions of an item. */
export function changedFieldsBetween(before: ItemOut, after: ItemOut): Set<string> {
  const changed = new Set<string>();
  for (const field of WATCHED_FIELDS) if (before[field] !== after[field]) changed.add(field);
  if (before.assignee?.id !== after.assignee?.id) changed.add("assignee");
  if (before.team.key !== after.team.key) changed.add("team");
  return changed;
}

export function ItemDetail({
  itemKey,
  onClose,
}: {
  itemKey: string | undefined;
  onClose: () => void;
}) {
  const item = useItem(itemKey);

  if (!itemKey) {
    return (
      <EmptyState title="Select a request">
        Pick one from the list to see it here. Use <kbd className="text-pencil">j</kbd> and{" "}
        <kbd className="text-pencil">k</kbd> to move, Enter to open.
      </EmptyState>
    );
  }
  if (
    item.isError &&
    (!item.data || (item.error instanceof ApiError && item.error.status === 404))
  ) {
    const gone = item.error instanceof ApiError && item.error.status === 404;
    return gone ? (
      <Gone itemKey={itemKey} />
    ) : (
      <ErrorState
        title={`Couldn't load ${itemKey}.`}
        error={item.error}
        onRetry={() => void item.refetch()}
      />
    );
  }
  if (!item.data) {
    return (
      <div className="space-y-3 p-6" aria-busy="true" aria-label="Loading request">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-6 w-4/5" />
        <Skeleton className="h-5 w-48" />
        <Skeleton className="mt-6 h-12 w-full" />
        <Skeleton className="h-9 w-40" />
      </div>
    );
  }
  return <Loaded key={item.data.key} item={item.data} onClose={onClose} />;
}

function Gone({ itemKey }: { itemKey: string }) {
  const qc = useQueryClient();
  useEffect(() => {
    // Drop it from every list too; the id isn't known here, so lists refresh on their own.
    qc.removeQueries({ queryKey: ["item", itemKey] });
    void qc.invalidateQueries({ queryKey: ["items", "list"] });
  }, [qc, itemKey]);
  return (
    <EmptyState
      title={`${itemKey} isn't available.`}
      action={
        <Link to="/items" className="text-body text-dispatch">
          Back to the queue
        </Link>
      }
    >
      It moved to another team, or you no longer have access to it.
    </EmptyState>
  );
}

function Loaded({ item, onClose }: { item: ItemOut; onClose: () => void }) {
  const qc = useQueryClient();
  const me = useMe();
  const now = useNow();
  const timeline = useRef<TimelineHandle>(null);
  const actions = useItemActions(item);

  // --- events: newest pages first, up to a bound; the track needs the whole ownership history ---
  const eventsQuery = useEvents(item.key);
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = eventsQuery;
  const loadedPages = eventsQuery.data?.pages.length ?? 0;
  useEffect(() => {
    if (hasNextPage && !isFetchingNextPage && loadedPages < MAX_EVENT_PAGES) void fetchNextPage();
  }, [hasNextPage, isFetchingNextPage, loadedPages, fetchNextPage]);

  const events = useMemo(() => {
    const seen = new Set<number>();
    const out: EventOut[] = [];
    for (const page of eventsQuery.data?.pages ?? []) {
      for (const event of page.items) {
        if (!seen.has(event.id)) {
          seen.add(event.id);
          out.push(event);
        }
      }
    }
    return out.sort((a, b) => a.id - b.id);
  }, [eventsQuery.data]);
  const names = useNames(item, events);

  // A new event on the item (my own action, or a poll that found someone else's) means the
  // timeline and the handoff track are behind: refetch them now instead of waiting for a poll.
  const lastSeenEvent = useRef(item.last_event_id);
  useEffect(() => {
    if (lastSeenEvent.current === item.last_event_id) return;
    lastSeenEvent.current = item.last_event_id;
    void qc.invalidateQueries({ queryKey: keys.events(item.key) });
  }, [qc, item.key, item.last_event_id]);

  // --- unread: remember what was unread when it opened, then tell the server it was seen ---
  const markRead = useCommand<undefined, ItemOut>({
    item,
    run: () =>
      unwrap(api.POST("/api/v1/items/{key}/read", { params: { path: { key: item.key } } })),
    onError: () => true, // a missed "seen" is harmless; no toast
  });
  // Loaded is keyed by item, so this is read once per open: the open itself clears it server-side.
  const [unreadSince] = useState(() => item.unread_since_event_id);
  const lastEventId = item.last_event_id;
  const markReadExecute = markRead.execute;
  useEffect(() => {
    void markReadExecute(undefined).catch(() => undefined);
    // Opening an item, and each new event while it stays open, counts as seen.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.key, lastEventId]);

  // --- live changes under an open item: notice, wash, announcement ---
  const previous = useRef<ItemOut | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [washed, setWashed] = useState<ReadonlySet<string>>(new Set());
  const myId = me.data?.user.id;
  useEffect(() => {
    const before = previous.current;
    previous.current = item;
    if (!before || compareVersion(item, before) <= 0) return;
    const byMe = item.last_event?.actor?.id === myId;
    if (byMe) return;
    const sentence = item.last_event
      ? lastEventSentence(item.last_event)
      : `${item.key} was updated.`;
    // An external change (a poll brought someone else's edit): a subscription, so state is set here.
    /* eslint-disable react-hooks/set-state-in-effect */
    setNotice(sentence);
    announce(`${item.key} updated. ${sentence}`);
    const fields = changedFieldsBetween(before, item);
    setWashed(fields);
    /* eslint-enable react-hooks/set-state-in-effect */
    const timer = window.setTimeout(() => {
      setWashed(new Set());
    }, 1300);
    return () => {
      window.clearTimeout(timer);
    };
  }, [item, myId]);

  // --- `m` focuses the comment box (DESIGN §6.3) ---
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (event.key !== "m" || event.metaKey || event.ctrlKey || event.altKey) return;
      if (["INPUT", "TEXTAREA", "SELECT"].includes(target?.tagName ?? "")) return;
      if (document.querySelector('[role="dialog"]')) return;
      event.preventDefault();
      timeline.current?.focusComposer();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  const segments = useMemo(
    () =>
      buildTrack({
        events,
        requester: item.requester,
        teamName: item.team.name,
        names,
        resolveName: names.resolve,
        createdAt: item.created_at,
        endedAt:
          item.status === "resolved" || item.status === "closed"
            ? (item.closed_at ?? item.resolved_at)
            : null,
        now,
        status: item.status,
      }),
    [events, item, names, now],
  );
  const ended = item.status === "resolved" || item.status === "closed";

  return (
    <article className="p-6" aria-label={`${item.key} ${item.title}`} data-item-key={item.key}>
      <div className="flex items-start justify-between gap-3">
        <p className="tnum text-meta text-pencil">{item.key}</p>
        <button
          type="button"
          aria-label="Close detail"
          onClick={onClose}
          className="inline-flex size-8 cursor-pointer items-center justify-center rounded-chip text-pencil hover:bg-dispatch-wash xl:hidden"
        >
          <X className="size-4" strokeWidth={1.75} />
        </button>
      </div>
      <TitleEditor item={item} washed={washed.has("title")} />
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-meta">
        <span
          className={
            washed.has("status")
              ? "wash inline-flex items-center gap-1.5"
              : "inline-flex items-center gap-1.5"
          }
        >
          <StatusIcon status={item.status} />
          {STATUS_LABEL[item.status]}
        </span>
        <PriorityGlyph priority={item.priority} />
        <span>{TYPE_LABEL[item.type]}</span>
        <span>{item.team.name}</span>
        {item.confidential && (
          <span className="inline-flex items-center gap-1 text-pencil">
            <Lock className="size-3.5" strokeWidth={1.75} aria-hidden />
            Confidential
          </span>
        )}
      </div>
      <div className="mt-3 flex items-center gap-2">
        <NextStepChip step={item.next_step} />
        {item.assignee && (
          <span className="inline-flex items-center gap-1.5 text-meta text-pencil">
            <Avatar person={item.assignee} size={20} />
            {item.assignee.name}
          </span>
        )}
      </div>

      {notice && (
        <div
          role="status"
          className="mt-4 flex items-start justify-between gap-3 rounded-panel bg-dispatch-wash px-3 py-2 text-meta"
        >
          <span>{notice}</span>
          <button
            type="button"
            aria-label="Dismiss"
            className="cursor-pointer text-pencil"
            onClick={() => {
              setNotice(null);
            }}
          >
            <X className="size-4" strokeWidth={1.75} />
          </button>
        </div>
      )}

      <HandoffTrack
        segments={segments}
        ended={ended}
        itemKey={item.key}
        ready={!eventsQuery.isPending}
      />

      <ActionBar item={item} actions={actions} members={names.members} />

      {actions.stale && (
        <StaleApprovalPanel item={item} stale={actions.stale} actions={actions} names={names} />
      )}
      <ApprovalPanel
        item={item}
        events={events}
        onRequestAgain={() => {
          void actions.run({ action: "request_approval" });
        }}
      />

      <Properties item={item} now={now} changedFields={washed} />
      <Description item={item} />
      <Timeline
        ref={timeline}
        item={item}
        me={me.data?.user ?? { id: "", name: "You" }}
        events={events}
        names={names}
        unreadSince={unreadSince}
        now={now}
        hasEarlier={hasNextPage && loadedPages >= MAX_EVENT_PAGES}
        loadingEarlier={isFetchingNextPage}
        onLoadEarlier={() => void fetchNextPage()}
        loading={eventsQuery.isPending}
        error={eventsQuery.error}
        onRetry={() => void eventsQuery.refetch()}
      />
    </article>
  );
}
