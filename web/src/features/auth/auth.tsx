/** Auth bootstrap (SPEC §5.4): GET /me decides. 401 anywhere → /login?next=<where I was>. */
import { useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect } from "react";
import { Navigate, Outlet, useLocation, useNavigate } from "react-router";

import { useMe } from "../../api/queries";
import { ErrorState, Skeleton } from "../../components/ui/States";
import { ApiError, onUnauthenticated } from "../../lib/api";

export function loginPath(next: string): string {
  return next === "/" || next.startsWith("/login")
    ? "/login"
    : `/login?next=${encodeURIComponent(next)}`;
}

/** Only same-origin paths: `next` comes from the URL and must not become an open redirect. */
export function safeNext(next: string | null): string {
  return next?.startsWith("/") && !next.startsWith("//") ? next : "/inbox";
}

/** Registers the 401 handler: drop cached data (it belongs to the old session) and go to login. */
export function useUnauthenticatedRedirect(): void {
  const qc = useQueryClient();
  const navigate = useNavigate();
  useEffect(() => {
    onUnauthenticated(() => {
      const here = window.location.pathname + window.location.search;
      if (window.location.pathname.startsWith("/login")) return;
      qc.clear();
      void navigate(loginPath(here), { replace: true });
    });
    return () => {
      onUnauthenticated(null);
    };
  }, [qc, navigate]);
}

function FullPageSkeleton() {
  return (
    <div className="flex h-full" aria-busy="true" aria-label="Loading Baton">
      <div className="w-58 shrink-0 space-y-3 p-4">
        <Skeleton className="h-6 w-20" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
      </div>
      <div className="flex-1 bg-sheet" />
    </div>
  );
}

export function RequireAuth({ children }: { children?: ReactNode }) {
  const me = useMe();
  const location = useLocation();
  if (me.isPending) return <FullPageSkeleton />;
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to={loginPath(location.pathname + location.search)} replace />;
  }
  if (me.isError) {
    return (
      <div className="mx-auto max-w-md pt-24">
        <ErrorState
          title="Couldn't reach Baton."
          error={me.error}
          onRetry={() => void me.refetch()}
        />
      </div>
    );
  }
  return children ?? <Outlet />;
}
