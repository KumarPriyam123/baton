/** Loading, empty and error states (I14). None of the screens may show a blank area. */
import type { ReactNode } from "react";

import { ApiError } from "../../lib/api";
import { describeError } from "../../lib/errors";
import { Button } from "./Button";
import { cn } from "./cn";

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} aria-hidden />;
}

/** Same footprint as a Strip, so content arriving doesn't shift the layout. */
export function StripSkeleton() {
  return (
    <div className="flex h-11 items-center gap-3 border-b border-rule px-4" aria-hidden>
      <Skeleton className="h-3 w-16" />
      <Skeleton className="h-3 flex-1 max-w-md" />
      <Skeleton className="ml-auto h-5 w-28" />
      <Skeleton className="size-6 rounded-full" />
      <Skeleton className="h-3 w-8" />
    </div>
  );
}

export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-start gap-3 px-6 py-12">
      <h2 className="text-section">{title}</h2>
      {children && <p className="max-w-md text-body text-pencil">{children}</p>}
      {action}
    </div>
  );
}

/** "Couldn't load X. [Try again]" with the request reference when the server gave one. */
export function ErrorState({
  title,
  error,
  onRetry,
}: {
  title: string;
  error: unknown;
  onRetry?: () => void;
}) {
  const reference = error instanceof ApiError ? error.requestId : null;
  return (
    <div role="alert" className="flex flex-col items-start gap-3 px-6 py-12">
      <h2 className="text-section text-p0">{title}</h2>
      <p className="max-w-md text-body text-pencil">{describeError(error)}</p>
      {reference && <p className="tnum text-meta text-pencil">Reference {reference}</p>}
      {onRetry && (
        <Button variant="secondary" onClick={onRetry}>
          Try again
        </Button>
      )}
    </div>
  );
}
