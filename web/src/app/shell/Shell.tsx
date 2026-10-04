/** App shell (DESIGN §3.1): rail on --desk, top bar, then the page. */
import {
  type ReactNode,
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import { Outlet, useLocation, useNavigate } from "react-router";

import { NewRequestDialog } from "../../features/create/NewRequestDialog";
import { useLiveUpdates } from "../../lib/useLiveUpdates";
import { useShortcuts } from "../../lib/useShortcuts";
import { Rail } from "./Rail";
import { TopBar } from "./TopBar";

interface ShellContext {
  openNewRequest: () => void;
}
const Ctx = createContext<ShellContext | null>(null);

export function useShell(): ShellContext {
  const value = useContext(Ctx);
  if (!value) throw new Error("useShell must be used inside <Shell>");
  return value;
}

export function Shell({ children }: { children?: ReactNode }) {
  const [creating, setCreating] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const location = useLocation();

  const openNewRequest = useCallback(() => {
    setCreating(true);
  }, []);

  const handlers = useMemo(
    () => ({
      newRequest: openNewRequest,
      focusSearch: () => searchRef.current?.focus(),
      goTo: (place: "inbox" | "queue") => void navigate(place === "inbox" ? "/inbox" : "/items"),
      // Esc closes an open detail (back to the list, filters kept).
      closeDetail: () => {
        if (/^\/items\/[^/]+/.test(location.pathname)) void navigate(`/items${location.search}`);
      },
    }),
    [navigate, openNewRequest, location.pathname, location.search],
  );
  useShortcuts(handlers);
  useLiveUpdates();

  const value = useMemo(() => ({ openNewRequest }), [openNewRequest]);

  return (
    <Ctx.Provider value={value}>
      <div className="grid h-dvh grid-cols-[56px_minmax(0,1fr)] lg:grid-cols-[232px_minmax(0,1fr)]">
        <Rail />
        <div className="flex min-h-0 min-w-0 flex-col bg-sheet">
          <TopBar searchRef={searchRef} onNewRequest={openNewRequest} />
          <main className="min-h-0 flex-1" id="main">
            {children ?? <Outlet />}
          </main>
        </div>
      </div>
      <NewRequestDialog open={creating} onOpenChange={setCreating} />
      {/* Announces live changes to screen readers (DESIGN §7). Toasts have their own region. */}
      <div aria-live="polite" role="status" className="sr-only-live" id="live-region" />
      <div aria-live="assertive" role="alert" className="sr-only-live" id="live-region-assertive" />
    </Ctx.Provider>
  );
}
