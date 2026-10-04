import { Link, Navigate, Outlet, createBrowserRouter, useRouteError } from "react-router";

import { RequireAuth, useUnauthenticatedRedirect } from "../features/auth/auth";
import { LoginPage } from "../features/auth/LoginPage";
import { DashboardPage } from "../features/dashboard/DashboardPage";
import { DecisionsPage } from "../features/decisions/DecisionsPage";
import { InboxPage } from "../features/inbox/InboxPage";
import { JobsPage } from "../features/jobs/JobsPage";
import { QueuePage } from "../features/queue/QueuePage";
import { EmptyState } from "../components/ui/States";
import { Button } from "../components/ui/Button";
import { Shell } from "./shell/Shell";

/** One boundary per route: a crash in the queue leaves the rail and top bar working. */
function RouteError() {
  const error = useRouteError();
  console.error(error);
  return (
    <div role="alert" className="flex flex-col items-start gap-3 px-6 py-12">
      <h2 className="text-section text-p0">This page hit a problem.</h2>
      <p className="max-w-md text-body text-pencil">
        Nothing was saved or lost. Reload the page, or go back to your inbox.
      </p>
      <div className="flex gap-2">
        <Button
          variant="secondary"
          onClick={() => {
            window.location.reload();
          }}
        >
          Reload
        </Button>
        <Link to="/inbox" className="inline-flex h-9 items-center px-3 text-body text-dispatch">
          Go to inbox
        </Link>
      </div>
    </div>
  );
}

function NotFound() {
  return (
    <EmptyState
      title="This page doesn't exist."
      action={
        <Link to="/inbox" className="text-body text-dispatch">
          Go to your inbox
        </Link>
      }
    >
      Check the address, or use the rail to find what you need.
    </EmptyState>
  );
}

function Root() {
  useUnauthenticatedRedirect();
  return <Outlet />;
}

export const router = createBrowserRouter([
  {
    element: <Root />,
    errorElement: <RouteError />,
    children: [
      { path: "/login", element: <LoginPage />, errorElement: <RouteError /> },
      {
        element: (
          <RequireAuth>
            <Shell />
          </RequireAuth>
        ),
        errorElement: <RouteError />,
        children: [
          { index: true, element: <Navigate to="/inbox" replace /> },
          { path: "inbox", element: <InboxPage />, errorElement: <RouteError /> },
          { path: "dashboard", element: <DashboardPage />, errorElement: <RouteError /> },
          { path: "decisions", element: <DecisionsPage />, errorElement: <RouteError /> },
          { path: "admin/jobs", element: <JobsPage />, errorElement: <RouteError /> },
          { path: "items/:key?", element: <QueuePage />, errorElement: <RouteError /> },
          { path: "*", element: <NotFound /> },
        ],
      },
    ],
  },
]);
