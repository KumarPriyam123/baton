/** Inbox (DESIGN §4.2): what needs me, in the order SPEC §7 gives. */
import { Link } from "react-router";

import type { components } from "../../api/generated";
import { useAttention } from "../../api/queries";
import { useShell } from "../../app/shell/Shell";
import { Button } from "../../components/ui/Button";
import { EmptyState, ErrorState, Skeleton, StripSkeleton } from "../../components/ui/States";
import { Strip } from "../../components/ui/Strip";
import { OPEN_STATUSES, type ListFilters, emptyFilters, serializeFilters } from "../../lib/filters";
import { useNow } from "../../lib/useNow";
import { usePrefetchItem } from "../queue/prefetch";
import { AssignToMe } from "../queue/AssignToMe";

type Section = components["schemas"]["AttentionSection"];

const CAP = 100;

/**
 * Where "Show all" goes. The queue has no filter for "pending approval for me" or "going quiet",
 * so those two open the nearest preset (KNOWN_LIMITATIONS).
 */
const OPEN = [...OPEN_STATUSES];
const SHOW_ALL: Record<Section["key"], Partial<ListFilters>> = {
  approvals: { status: ["awaiting_approval"] },
  urgent: { assignee: "me", status: OPEN, priority: [0, 1] },
  unowned: { assignee: "none", status: ["new"] },
  quiet: { assignee: "me", status: ["in_progress", "blocked"], sort: "updated" },
  requests: { requester: "me", sort: "updated" },
};

function showAllLink(key: Section["key"]): string {
  return `/items?${serializeFilters({ ...emptyFilters(), ...SHOW_ALL[key] }).toString()}`;
}

function InboxSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading your inbox">
      {[0, 1, 2].map((i) => (
        <section key={i} className="mb-6">
          <div className="flex h-10 items-center px-4">
            <Skeleton className="h-4 w-40" />
          </div>
          {[0, 1, 2].map((j) => (
            <StripSkeleton key={j} />
          ))}
        </section>
      ))}
    </div>
  );
}

export function InboxPage() {
  const attention = useAttention();
  const { openNewRequest } = useShell();
  const prefetchItem = usePrefetchItem();
  const now = useNow();

  if (attention.isPending) return <InboxSkeleton />;
  if (attention.isError) {
    return (
      <ErrorState
        title="Couldn't load your inbox."
        error={attention.error}
        onRetry={() => void attention.refetch()}
      />
    );
  }

  const sections = attention.data.sections.filter((s) => s.count > 0 || s.items.length > 0);

  return (
    <div className="h-full overflow-y-auto">
      <h1 className="sr-only-live">Inbox</h1>
      {sections.length === 0 ? (
        <EmptyState
          title="Nothing needs you right now."
          action={
            <Button variant="primary" onClick={openNewRequest}>
              New request
            </Button>
          }
        >
          New requests for your teams will show up here.
        </EmptyState>
      ) : (
        sections.map((section) => (
          <section key={section.key} aria-labelledby={`inbox-${section.key}`} className="mb-6">
            <div className="flex h-10 items-baseline justify-between px-4 pt-3">
              <h2 id={`inbox-${section.key}`} className="text-section">
                {section.title}{" "}
                <span className="tnum font-body text-pencil">
                  {section.count >= CAP ? `${String(CAP)}+` : section.count}
                </span>
              </h2>
              {section.count > section.items.length && (
                <Link to={showAllLink(section.key)} className="text-meta text-dispatch">
                  Show all
                </Link>
              )}
            </div>
            <div role="list" className="mt-1 border-t border-rule">
              {section.items.map((item) => (
                <Strip
                  key={item.id}
                  item={item}
                  to={`/items/${item.key}`}
                  now={now}
                  onPrefetch={prefetchItem}
                  actions={<AssignToMe item={item} />}
                />
              ))}
            </div>
          </section>
        ))
      )}
    </div>
  );
}
