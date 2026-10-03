import { Plus, Search } from "lucide-react";
import { type RefObject, useEffect, useState } from "react";
import { useLocation, useNavigate, useSearchParams } from "react-router";

import { Button } from "../../components/ui/Button";
import { Kbd } from "../../components/ui/FilterToken";

const SEARCH_DEBOUNCE_MS = 200;
const KEY_PATTERN = /^[A-Za-z]{2,5}-\d+$/;

/**
 * Search as you type (DESIGN §4.3): the query lives in the URL as `q`, debounced 200 ms, so it is
 * shareable and survives a refresh. A key like PAY-142 plus Enter jumps straight to the item.
 */
export function TopBar({
  searchRef,
  onNewRequest,
}: {
  searchRef: RefObject<HTMLInputElement | null>;
  onNewRequest: () => void;
}) {
  const navigate = useNavigate();
  const location = useLocation();
  const [params, setParams] = useSearchParams();
  const onQueue = location.pathname.startsWith("/items");
  const urlQ = onQueue ? (params.get("q") ?? "") : "";
  const [text, setText] = useState(urlQ);

  // The URL changed from outside (Clear filters, a rail link): follow it. Adjusting state while
  // rendering is React's recommended way to reset state when a value changes.
  const [seenUrlQ, setSeenUrlQ] = useState(urlQ);
  if (urlQ !== seenUrlQ) {
    setSeenUrlQ(urlQ);
    setText(urlQ);
  }

  // Debounce typing into the URL. Other filters stay; `replace` keeps Back usable.
  useEffect(() => {
    const trimmed = text.trim();
    if (trimmed === urlQ.trim()) return;
    const timer = window.setTimeout(() => {
      if (onQueue) {
        const next = new URLSearchParams(params);
        if (trimmed) next.set("q", trimmed);
        else next.delete("q");
        setParams(next, { replace: true });
      } else if (trimmed) {
        void navigate(`/items?q=${encodeURIComponent(trimmed)}`);
      }
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [text, urlQ, onQueue, params, setParams, navigate]);

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-rule px-4">
      <form
        role="search"
        className="relative max-w-xl flex-1"
        onSubmit={(event) => {
          event.preventDefault();
          const trimmed = text.trim();
          if (KEY_PATTERN.test(trimmed)) void navigate(`/items/${trimmed.toUpperCase()}`);
        }}
      >
        <Search
          className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-pencil"
          strokeWidth={1.75}
          aria-hidden
        />
        <input
          ref={searchRef}
          type="search"
          aria-label="Search requests"
          placeholder="Search requests"
          value={text}
          onChange={(event) => {
            setText(event.target.value);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setText("");
              event.currentTarget.blur();
            }
          }}
          className="h-9 w-full rounded-chip border border-rule bg-sheet pr-10 pl-9 text-body text-ink placeholder:text-pencil"
        />
        <span className="pointer-events-none absolute top-1/2 right-2 -translate-y-1/2">
          <Kbd>/</Kbd>
        </span>
      </form>
      <Button variant="primary" onClick={onNewRequest} className="ml-auto">
        <Plus className="size-4" strokeWidth={1.75} aria-hidden />
        New request
      </Button>
    </header>
  );
}
