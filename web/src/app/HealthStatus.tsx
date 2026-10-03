import { useEffect, useState } from "react";

import { fetchHealth } from "../api/health";

type State = { kind: "loading" } | { kind: "ok"; status: string } | { kind: "error" };

/** Phase 0 placeholder: proves the SPA reaches the API through nginx. */
export function HealthStatus() {
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const controller = new AbortController();
    fetchHealth(controller.signal)
      .then((health) => {
        setState({ kind: "ok", status: health.status });
      })
      .catch(() => {
        if (!controller.signal.aborted) setState({ kind: "error" });
      });
    return () => {
      controller.abort();
    };
  }, []);

  return (
    <p role="status" aria-live="polite" className="text-stone-700">
      {state.kind === "loading" && "Checking the API…"}
      {state.kind === "ok" && `API status: ${state.status}`}
      {state.kind === "error" && "Can't reach the API. Is the stack running?"}
    </p>
  );
}
