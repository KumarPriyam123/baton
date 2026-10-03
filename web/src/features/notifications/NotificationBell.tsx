/** The bell in the rail footer (DESIGN §4.11): unread badge, a popover of the latest notifications
 * grouped by item, mark all read. Opening an item from here marks its notifications read. */
import { Bell } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router";

import type { components } from "../../api/generated";
import { useMarkRead, useNotifications, useUnreadCount } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { PopoverContent, PopoverRoot, PopoverTrigger } from "../../components/ui/Popover";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { type EventKind, eventPhrase, firstName } from "../../lib/eventText";
import { ageShort, fullTime } from "../../lib/format";
import { unreadBadge } from "../../lib/notifications";
import { useNow } from "../../lib/useNow";

type Notification = components["schemas"]["NotificationOut"];

interface Group {
  key: string;
  title: string;
  rows: Notification[];
}

/** Consecutive notifications about one item share a heading; the list stays newest first. */
function groupByItem(rows: Notification[]): Group[] {
  const groups: Group[] = [];
  for (const row of rows) {
    const last = groups[groups.length - 1];
    if (last?.key === row.item_key) last.rows.push(row);
    else groups.push({ key: row.item_key, title: row.item_title, rows: [row] });
  }
  return groups;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const unread = useUnreadCount();
  const badge = unreadBadge(unread.data);

  return (
    <PopoverRoot open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className="flex h-9 w-full cursor-pointer items-center gap-3 rounded-chip px-2 text-left text-body hover:bg-dispatch-wash"
        aria-label={badge ? `Notifications, ${badge} unread` : "Notifications"}
        title="Notifications"
      >
        <span className="relative">
          <Bell className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
          {badge && (
            <span
              className="absolute -right-1.5 -top-1.5 size-2 rounded-full bg-p0 lg:hidden"
              aria-hidden
            />
          )}
        </span>
        <span className="hidden lg:inline">Notifications</span>
        {badge && (
          <span className="tnum ml-auto hidden rounded-chip bg-p0-wash px-1.5 text-small font-strong text-p0 lg:inline">
            {badge}
          </span>
        )}
      </PopoverTrigger>
      <PopoverContent
        label="Notifications"
        side="right"
        align="end"
        className="w-[min(420px,calc(100vw-24px))] p-0"
      >
        <NotificationList
          open={open}
          onClose={() => {
            setOpen(false);
          }}
        />
      </PopoverContent>
    </PopoverRoot>
  );
}

function NotificationList({ open, onClose }: { open: boolean; onClose: () => void }) {
  const list = useNotifications(open);
  const markRead = useMarkRead();
  const navigate = useNavigate();
  const now = useNow();

  const openItem = (group: Group) => {
    const ids = group.rows.filter((r) => r.read_at === null).map((r) => r.id);
    if (ids.length > 0) markRead.mutate({ ids, all: false });
    onClose();
    void navigate(`/items/${group.key}`);
  };

  const hasUnread = list.data?.items.some((r) => r.read_at === null) ?? false;

  return (
    <div className="flex max-h-[min(520px,calc(100dvh-48px))] flex-col">
      <div className="flex h-12 shrink-0 items-center justify-between border-b border-rule px-4">
        <h2 className="text-section">Notifications</h2>
        <Button
          variant="quiet"
          className="h-8"
          disabled={!hasUnread}
          pending={markRead.isPending}
          onClick={() => {
            markRead.mutate({ all: true });
          }}
        >
          Mark all read
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {list.isPending ? (
          <div
            aria-busy="true"
            aria-label="Loading notifications"
            className="flex flex-col gap-3 p-4"
          >
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex flex-col gap-2">
                <Skeleton className="h-4 w-56" />
                <Skeleton className="h-3 w-40" />
              </div>
            ))}
          </div>
        ) : list.isError ? (
          <ErrorState
            title="Couldn't load notifications."
            error={list.error}
            onRetry={() => void list.refetch()}
          />
        ) : list.data.items.length === 0 ? (
          <EmptyState title="You're all caught up.">
            Updates on requests you raised, own or watch will show up here.
          </EmptyState>
        ) : (
          <ul className="m-0 list-none p-0">
            {groupByItem(list.data.items).map((group, index) => (
              <li
                key={`${group.key}-${String(index)}`}
                className="border-b border-rule last:border-b-0"
              >
                <button
                  type="button"
                  onClick={() => {
                    openItem(group);
                  }}
                  className="block w-full cursor-pointer px-4 py-3 text-left hover:bg-dispatch-wash"
                >
                  <span className="flex items-baseline gap-2">
                    <span className="tnum text-meta text-pencil">{group.key}</span>
                    <span className="truncate text-body font-strong">{group.title}</span>
                  </span>
                  <span className="mt-1 flex flex-col gap-0.5">
                    {group.rows.map((row) => (
                      <span key={row.id} className="flex items-baseline gap-3 text-meta">
                        <span
                          className={cn(
                            "min-w-0 flex-1 truncate",
                            row.read_at === null ? "text-ink" : "text-pencil",
                          )}
                        >
                          {row.read_at === null && <span className="sr-only-live">Unread. </span>}
                          {row.actor ? firstName(row.actor.name) : "Baton"}{" "}
                          {eventPhrase(row.kind as EventKind)}
                        </span>
                        <time
                          dateTime={row.created_at}
                          title={fullTime(row.created_at)}
                          className="tnum w-10 shrink-0 text-right text-pencil"
                        >
                          {ageShort(row.created_at, now)}
                        </time>
                      </span>
                    ))}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
