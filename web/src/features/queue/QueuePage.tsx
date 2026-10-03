/**
 * Queue (DESIGN §4.3): filters in the URL, infinite virtualised list, search as you type.
 * Selecting a strip puts its key in the URL (/items/PAY-142?…filters) so links and Back work.
 */
import { useVirtualizer } from "@tanstack/react-virtual";
import { useEffect, useMemo, useRef } from "react";
import { useLocation, useNavigate, useParams, useSearchParams } from "react-router";

import { useItemList, useTeams } from "../../api/queries";
import { useShell } from "../../app/shell/Shell";
import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { EmptyState, ErrorState, StripSkeleton } from "../../components/ui/States";
import { Strip } from "../../components/ui/Strip";
import {
  type ListFilters,
  applyPatch,
  emptyFilters,
  hasNarrowing,
  parseFilters,
  serializeFilters,
} from "../../lib/filters";
import type { ItemOut } from "../../lib/api";
import { useNow } from "../../lib/useNow";
import { AssignToMe } from "./AssignToMe";
import { DetailPane } from "./DetailPane";
import { FilterBar } from "./FilterBar";
import { usePrefetchItem } from "./prefetch";

const ROW_HEIGHT = 44;
const LOAD_AHEAD = 10; // fetch the next page when the user is 10 rows from the end (DESIGN §4.3)

export function QueuePage() {
  const { key } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const teams = useTeams();

  const setFilters = (next: ListFilters) => {
    setParams(serializeFilters(next));
  };

  return (
    <div className="flex h-full min-h-0">
      <section aria-label="Requests" className="flex min-h-0 min-w-0 flex-1 flex-col">
        <h1 className="sr-only-live">Requests</h1>
        <FilterBar filters={filters} teams={teams.data ?? []} onChange={setFilters} />
        <ResultList
          filters={filters}
          selectedKey={key?.toUpperCase()}
          search={location.search}
          onClear={() => {
            // Keep the search text when clearing the rest: "search all teams".
            setFilters(filters.q ? applyPatch(emptyFilters(), { q: filters.q }) : emptyFilters());
          }}
          onClearSearch={() => {
            setFilters(applyPatch(filters, { q: undefined }));
          }}
        />
      </section>
      <aside
        aria-label="Request detail"
        className={cn(
          "min-h-0 overflow-y-auto border-l border-rule bg-sheet xl:static xl:block xl:w-[560px] xl:shrink-0 xl:shadow-none",
          key ? "fixed inset-y-0 right-0 z-30 block w-[560px] max-w-full shadow-float" : "hidden",
        )}
      >
        <DetailPane
          itemKey={key?.toUpperCase()}
          onClose={() => void navigate(`/items${location.search}`)}
        />
      </aside>
    </div>
  );
}

function ResultList({
  filters,
  selectedKey,
  search,
  onClear,
  onClearSearch,
}: {
  filters: ListFilters;
  selectedKey: string | undefined;
  search: string;
  onClear: () => void;
  onClearSearch: () => void;
}) {
  const list = useItemList(filters);
  const prefetchItem = usePrefetchItem();
  const { openNewRequest } = useShell();
  const parentRef = useRef<HTMLDivElement>(null);
  const now = useNow();

  // Keyset pages can overlap if items move between fetches: one row per item.
  const items = useMemo(() => {
    const seen = new Set<string>();
    const out: ItemOut[] = [];
    for (const page of list.data?.pages ?? []) {
      for (const item of page.items) {
        if (!seen.has(item.id)) {
          seen.add(item.id);
          out.push(item);
        }
      }
    }
    return out;
  }, [list.data]);

  const virtualizer = useVirtualizer({
    count: items.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: LOAD_AHEAD,
  });
  const virtualItems = virtualizer.getVirtualItems();
  const lastIndex = virtualItems[virtualItems.length - 1]?.index ?? -1;

  const { hasNextPage, isFetchingNextPage, fetchNextPage } = list;
  useEffect(() => {
    if (lastIndex >= items.length - LOAD_AHEAD && hasNextPage && !isFetchingNextPage) {
      void fetchNextPage();
    }
  }, [lastIndex, items.length, hasNextPage, isFetchingNextPage, fetchNextPage]);

  // New filters start at the top.
  const filterKey = serializeFilters(filters).toString();
  useEffect(() => {
    parentRef.current?.scrollTo({ top: 0 });
  }, [filterKey]);

  if (list.isPending) {
    return (
      <div aria-busy="true" aria-label="Loading requests">
        {Array.from({ length: 10 }, (_, i) => (
          <StripSkeleton key={i} />
        ))}
      </div>
    );
  }
  if (list.isError && items.length === 0) {
    return (
      <ErrorState
        title="Couldn't load requests."
        error={list.error}
        onRetry={() => void list.refetch()}
      />
    );
  }
  if (items.length === 0) {
    if (filters.q) {
      return (
        <EmptyState
          title={`No requests match “${filters.q}”.`}
          action={
            hasNarrowing(applyPatch(filters, { q: undefined })) ? (
              <Button variant="secondary" onClick={onClear}>
                Search all teams
              </Button>
            ) : (
              <Button variant="secondary" onClick={onClearSearch}>
                Clear search
              </Button>
            )
          }
        >
          Check the spelling or search all teams.
        </EmptyState>
      );
    }
    if (hasNarrowing(filters)) {
      return (
        <EmptyState
          title="No requests match these filters."
          action={
            <Button variant="secondary" onClick={onClear}>
              Clear filters
            </Button>
          }
        />
      );
    }
    return (
      <EmptyState
        title="No requests yet."
        action={
          <Button variant="primary" onClick={openNewRequest}>
            New request
          </Button>
        }
      >
        Raise the first one and it will show up here.
      </EmptyState>
    );
  }

  return (
    <div
      ref={parentRef}
      className={cn("min-h-0 flex-1 overflow-y-auto", list.isPlaceholderData && "opacity-60")}
      aria-busy={list.isFetching || undefined}
    >
      <div role="list" className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
        {virtualItems.map((row) => {
          const item = items[row.index];
          if (!item) return null;
          return (
            <div
              key={item.id}
              className="absolute top-0 left-0 w-full"
              style={{ height: row.size, transform: `translateY(${String(row.start)}px)` }}
            >
              <Strip
                item={item}
                to={`/items/${item.key}${search}`}
                selected={item.key === selectedKey}
                now={now}
                highlight={filters.q}
                onPrefetch={prefetchItem}
                actions={
                  item.allowed_actions.includes("claim") ? <AssignToMe item={item} /> : undefined
                }
              />
            </div>
          );
        })}
      </div>
      {isFetchingNextPage && <StripSkeleton />}
    </div>
  );
}
