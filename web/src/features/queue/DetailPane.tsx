/**
 * The right-hand pane. The full detail screen (timeline, actions, approvals) is the next build
 * step; this shows what the list already knows so a selected strip is never a dead end.
 */
import { X } from "lucide-react";
import { Link } from "react-router";

import { useItem } from "../../api/queries";
import { NextStepChip, StatusIcon } from "../../components/ui/Strip";
import { Avatar } from "../../components/ui/Avatar";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { ApiError } from "../../lib/api";
import { PRIORITY_LABEL, STATUS_LABEL, TYPE_LABEL } from "../../lib/format";

export function DetailPane({
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
  if (item.isPending) {
    return (
      <div className="space-y-3 p-6" aria-busy="true" aria-label="Loading request">
        <Skeleton className="h-4 w-20" />
        <Skeleton className="h-6 w-4/5" />
        <Skeleton className="h-5 w-48" />
      </div>
    );
  }
  if (item.isError) {
    const gone = item.error instanceof ApiError && item.error.status === 404;
    return gone ? (
      <EmptyState
        title={`${itemKey} isn't available.`}
        action={
          <Link to="/items" className="text-body text-dispatch">
            Back to the queue
          </Link>
        }
      >
        This item doesn't exist or you no longer have access.
      </EmptyState>
    ) : (
      <ErrorState
        title={`Couldn't load ${itemKey}.`}
        error={item.error}
        onRetry={() => void item.refetch()}
      />
    );
  }

  const data = item.data;
  return (
    <article className="p-6">
      <div className="flex items-start justify-between gap-3">
        <p className="tnum text-meta text-pencil">{data.key}</p>
        <button
          type="button"
          aria-label="Close detail"
          onClick={onClose}
          className="inline-flex size-8 cursor-pointer items-center justify-center rounded-chip text-pencil hover:bg-dispatch-wash xl:hidden"
        >
          <X className="size-4" strokeWidth={1.75} />
        </button>
      </div>
      <h2 className="mt-1 text-item">{data.title}</h2>
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2 text-meta">
        <span className="inline-flex items-center gap-1.5">
          <StatusIcon status={data.status} />
          {STATUS_LABEL[data.status]}
        </span>
        <span>{PRIORITY_LABEL[data.priority]}</span>
        <span>{TYPE_LABEL[data.type]}</span>
        <span>{data.team.name}</span>
      </div>
      <div className="mt-4 flex items-center gap-2">
        <NextStepChip step={data.next_step} />
      </div>
      <dl className="mt-6 grid grid-cols-[96px_1fr] gap-y-3 text-body">
        <dt className="text-pencil">Owner</dt>
        <dd className="flex items-center gap-2">
          <Avatar person={data.assignee} />
          {data.assignee?.name ?? "Nobody yet"}
        </dd>
        <dt className="text-pencil">Requester</dt>
        <dd className="flex items-center gap-2">
          <Avatar person={data.requester} />
          {data.requester.name}
        </dd>
      </dl>
    </article>
  );
}
