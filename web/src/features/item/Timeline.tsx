/**
 * Timeline (DESIGN §4.4): events and comments interleaved, oldest first, newest at the bottom.
 * Filter All · Comments · Decisions. A divider marks "New since you last looked". The composer is
 * pinned at the bottom; ⌘/Ctrl + Enter sends. A comment appears at once as "Sending…" and keeps its
 * Idempotency-Key, so Retry after a failure can't post it twice (I6).
 */
import { type InfiniteData, useQueryClient } from "@tanstack/react-query";
import { forwardRef, useImperativeHandle, useRef, useState } from "react";

import type { components } from "../../api/generated";
import { keys } from "../../api/keys";
import { Avatar } from "../../components/ui/Avatar";
import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { Textarea } from "../../components/ui/Field";
import { Markdown } from "../../components/ui/Markdown";
import { type ItemOut, api, unwrap } from "../../lib/api";
import { describeError } from "../../lib/errors";
import { type EventOut, type NameLookup, actorName, describeEvent } from "../../lib/eventText";
import { ageShort, fullTime } from "../../lib/format";
import { newIdempotencyKey, useCommand } from "../../lib/useCommand";

type CommentCreated = components["schemas"]["CommentCreated"];
type EventsPage = components["schemas"]["EventsPage"];
type Filter = "all" | "comments" | "decisions";

const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "comments", label: "Comments" },
  { value: "decisions", label: "Decisions" },
];

interface PendingComment {
  localId: string;
  body: string;
  key: string;
  state: "sending" | "failed";
  error?: string;
}

export interface TimelineHandle {
  focusComposer: () => void;
}

function matches(event: EventOut, filter: Filter): boolean {
  if (filter === "comments") return event.kind === "commented";
  if (filter === "decisions") return event.is_decision;
  return true;
}

export const Timeline = forwardRef<
  TimelineHandle,
  {
    item: ItemOut;
    me: { id: string; name: string };
    events: readonly EventOut[]; // oldest first
    names: NameLookup;
    /** `unread_since_event_id` as it was when this item was opened (the open itself clears it). */
    unreadSince: number | null;
    now: number;
    hasEarlier: boolean;
    loadingEarlier: boolean;
    onLoadEarlier: () => void;
    loading: boolean;
    error: unknown;
    onRetry: () => void;
  }
>(function Timeline(
  {
    item,
    me,
    events,
    names,
    unreadSince,
    now,
    hasEarlier,
    loadingEarlier,
    onLoadEarlier,
    loading,
    error,
    onRetry,
  },
  ref,
) {
  const [filter, setFilter] = useState<Filter>("all");
  const [pending, setPending] = useState<PendingComment[]>([]);
  const [text, setText] = useState("");
  const composer = useRef<HTMLTextAreaElement>(null);
  const qc = useQueryClient();
  useImperativeHandle(ref, () => ({ focusComposer: () => composer.current?.focus() }));

  const post = useCommand<{ body: string }, CommentCreated>({
    item,
    run: ({ body }, ctx) =>
      unwrap(
        api.POST("/api/v1/items/{key}/comments", {
          params: { path: { key: item.key }, header: { "Idempotency-Key": ctx.idempotencyKey } },
          body: { body },
        }),
      ),
    toItem: (created) => created.item,
    onError: () => true, // shown on the comment itself, with Retry
  });

  const send = async (entry: PendingComment) => {
    try {
      const created = await post.execute({ body: entry.body }, { idempotencyKey: entry.key });
      const event: EventOut = {
        id: created.comment.event_id,
        kind: "commented",
        actor: created.comment.author,
        item_version: created.item.version,
        data: {},
        reason: null,
        is_decision: false,
        created_at: created.comment.created_at,
        comment_body: created.comment.body,
      };
      // Put it in the timeline now; the next poll confirms it. Never twice (same event id).
      qc.setQueryData<InfiniteData<EventsPage>>(keys.events(item.key), (data) => {
        if (!data || data.pages.some((p) => p.items.some((e) => e.id === event.id))) return data;
        const [first, ...rest] = data.pages;
        return first
          ? { ...data, pages: [{ ...first, items: [event, ...first.items] }, ...rest] }
          : data;
      });
      setPending((list) => list.filter((p) => p.localId !== entry.localId));
    } catch (error) {
      setPending((list) =>
        list.map((p) =>
          p.localId === entry.localId ? { ...p, state: "failed", error: describeError(error) } : p,
        ),
      );
    }
  };

  const submit = () => {
    const body = text.trim();
    if (body === "") return;
    const entry: PendingComment = {
      localId: newIdempotencyKey(),
      body,
      key: newIdempotencyKey(),
      state: "sending",
    };
    setPending((list) => [...list, entry]);
    setText("");
    void send(entry);
  };

  const canComment = item.allowed_actions.includes("comment");
  const visible = events.filter((e) => matches(e, filter));
  const firstNewId = unreadSince === null ? undefined : visible.find((e) => e.id > unreadSince)?.id;

  return (
    <section aria-labelledby="timeline-heading" className="mt-8">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h2 id="timeline-heading" className="text-section">
          Timeline
        </h2>
        <div role="group" aria-label="Filter timeline" className="flex gap-1">
          {FILTERS.map((f) => (
            <button
              key={f.value}
              type="button"
              aria-pressed={filter === f.value}
              onClick={() => {
                setFilter(f.value);
              }}
              className={cn(
                "h-7 cursor-pointer rounded-chip px-2.5 text-meta font-strong",
                filter === f.value
                  ? "bg-dispatch-wash text-ink"
                  : "text-pencil hover:bg-dispatch-wash",
              )}
            >
              {f.label}
            </button>
          ))}
        </div>
      </div>

      {loading && (
        <p className="text-meta text-pencil" role="status">
          Loading the timeline…
        </p>
      )}
      {!loading && error !== null && error !== undefined && events.length === 0 && (
        <div role="alert" className="text-meta">
          <p className="text-p0">Couldn't load the timeline.</p>
          <Button variant="secondary" className="mt-2 h-7 px-2 text-meta" onClick={onRetry}>
            Try again
          </Button>
        </div>
      )}
      {!loading && visible.length === 0 && pending.length === 0 && !error && (
        <p className="text-body text-pencil">
          {filter === "all"
            ? "Nothing has happened yet."
            : filter === "comments"
              ? "No comments yet."
              : "No decisions recorded yet."}
        </p>
      )}
      {hasEarlier && (
        <Button
          variant="quiet"
          className="mb-2 h-7 px-2 text-meta"
          pending={loadingEarlier}
          onClick={onLoadEarlier}
        >
          Show earlier events
        </Button>
      )}

      <ol className="flex flex-col" data-testid="timeline">
        {visible.map((event) => (
          <li key={event.id}>
            {event.id === firstNewId && (
              <div
                role="separator"
                aria-label="New since you last looked"
                className="my-3 flex items-center gap-3 text-small font-strong text-dispatch"
              >
                <span className="h-px flex-1 bg-dispatch" />
                New since you last looked
                <span className="h-px flex-1 bg-dispatch" />
              </div>
            )}
            <EventRow event={event} names={names} now={now} />
          </li>
        ))}
        {filter !== "decisions" &&
          pending.map((entry) => (
            <li key={entry.localId} className="flex gap-3 py-2" data-testid="pending-comment">
              <Avatar person={me} size={24} />
              <div className="min-w-0 flex-1">
                <p className="text-meta text-pencil">
                  You ·{" "}
                  {entry.state === "sending" ? (
                    <span role="status">Sending…</span>
                  ) : (
                    <span role="alert" className="text-p0">
                      Not sent. {entry.error}
                    </span>
                  )}
                </p>
                <p className="mt-1 whitespace-pre-wrap text-body">{entry.body}</p>
                {entry.state === "failed" && (
                  <div className="mt-1 flex gap-2">
                    <Button
                      variant="secondary"
                      className="h-7 px-2 text-meta"
                      onClick={() => {
                        setPending((list) =>
                          list.map((p) =>
                            p.localId === entry.localId ? { ...p, state: "sending" } : p,
                          ),
                        );
                        void send(entry);
                      }}
                    >
                      Retry
                    </Button>
                    <Button
                      variant="quiet"
                      className="h-7 px-2 text-meta"
                      onClick={() => {
                        setPending((list) => list.filter((p) => p.localId !== entry.localId));
                      }}
                    >
                      Discard
                    </Button>
                  </div>
                )}
              </div>
            </li>
          ))}
      </ol>

      <div className="sticky bottom-0 -mx-6 mt-4 border-t border-rule bg-sheet px-6 py-3">
        {canComment ? (
          <form
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
            className="flex flex-col gap-2"
          >
            <Textarea
              ref={composer}
              aria-label="Add a comment"
              placeholder="Add a comment"
              value={text}
              className="min-h-16"
              maxLength={10_000}
              onChange={(e) => {
                setText(e.target.value);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                  event.preventDefault();
                  submit();
                }
              }}
            />
            <div className="flex items-center justify-between">
              <span className="text-small text-pencil">Markdown works. Ctrl + Enter sends.</span>
              <Button type="submit" variant="primary" disabled={text.trim() === ""}>
                Comment
              </Button>
            </div>
          </form>
        ) : (
          <p className="text-meta text-pencil">You can read this timeline but not comment on it.</p>
        )}
      </div>
    </section>
  );
});

function EventRow({ event, names, now }: { event: EventOut; names: NameLookup; now: number }) {
  const when = (
    <time
      dateTime={event.created_at}
      title={fullTime(event.created_at)}
      className="tnum shrink-0 text-small text-pencil"
    >
      {ageShort(event.created_at, now)}
    </time>
  );

  if (event.kind === "commented") {
    return (
      <div className="flex gap-3 py-2">
        <Avatar person={event.actor} size={24} />
        <div className="min-w-0 flex-1">
          <p className="flex items-baseline justify-between gap-2 text-meta">
            <span className="font-strong">{actorName(event)}</span>
            {when}
          </p>
          <div className="mt-1">
            <Markdown>{event.comment_body ?? ""}</Markdown>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex gap-3 py-1.5">
      <span className="flex w-6 justify-center pt-2" aria-hidden>
        <span
          className={cn("size-1.5 rounded-full", event.is_decision ? "bg-dispatch" : "bg-pencil")}
        />
      </span>
      <div className="min-w-0 flex-1">
        <p className="flex items-baseline justify-between gap-2 text-meta">
          <span>
            <span className="font-strong">{actorName(event)}</span> {describeEvent(event, names)}
          </span>
          {when}
        </p>
        {event.reason && (
          <blockquote className="mt-1 border-l-2 border-rule pl-3 text-meta text-pencil">
            {event.reason}
          </blockquote>
        )}
      </div>
    </div>
  );
}
