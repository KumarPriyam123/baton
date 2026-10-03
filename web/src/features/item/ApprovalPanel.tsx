/**
 * Approval panel and the stale-approve flow (DESIGN §4.4, §6.2; SPEC §4.4).
 *
 * The panel says what happened to the approval in words. When Approve answers 412 the approver
 * sees "changed since you opened it", can review exactly what changed (word diff for text), and
 * then approves THIS version: the retry carries the version the server just sent back.
 */
import { useState } from "react";

import { Button } from "../../components/ui/Button";
import { DiffView } from "../../components/ui/DiffView";
import type { ItemOut } from "../../lib/api";
import { type EventOut, type NameLookup, actorName, describeEvent } from "../../lib/eventText";
import { fullTime } from "../../lib/format";
import type { ChangeEvent } from "../../lib/rebase";
import type { ItemActions, StaleApproval } from "./useItemActions";

function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function latest(events: readonly EventOut[], kind: EventOut["kind"]): EventOut | undefined {
  for (let i = events.length - 1; i >= 0; i -= 1) if (events[i]?.kind === kind) return events[i];
  return undefined;
}

export function ApprovalPanel({
  item,
  events,
  onRequestAgain,
}: {
  item: ItemOut;
  events: readonly EventOut[];
  onRequestAgain: () => void;
}) {
  const approval = item.approval;
  if (!approval || approval.status === "cancelled") return null;

  const by = approval.decided_by?.name ?? "someone";
  const canRequest = item.allowed_actions.includes("request_approval");
  let body: string;
  let tone: "info" | "warn" = "info";

  switch (approval.status) {
    case "pending":
      body = `Approval requested by ${approval.requested_by.name} at ${clock(approval.requested_at)}. Waiting for a lead to decide.`;
      break;
    case "approved":
      body = `Approved by ${by}${approval.decided_at ? ` at ${clock(approval.decided_at)}` : ""}. It covers the title and description as they are now.`;
      break;
    case "rejected": {
      const reason = latest(events, "approval_rejected")?.reason;
      body = `Rejected by ${by}${reason ? `: “${reason}”` : "."}`;
      tone = "warn";
      break;
    }
    case "invalidated": {
      const data = latest(events, "approval_invalidated")?.data;
      const fields = Array.isArray(data?.fields)
        ? (data.fields as string[]).join(" and ")
        : "content";
      const who = approval.decided_by ? ` after ${by} approved it` : "";
      body = `Approval withdrawn: the ${fields} changed${who}. Request approval again before resolving.`;
      tone = "warn";
      break;
    }
  }

  return (
    <section
      aria-label="Approval"
      className={
        tone === "warn"
          ? "mt-5 rounded-panel border border-rule bg-p1-wash p-3 text-body"
          : "mt-5 rounded-panel border border-rule bg-desk p-3 text-body"
      }
    >
      <p>{body}</p>
      {approval.status === "invalidated" && canRequest && (
        <Button variant="secondary" className="mt-2 h-8" onClick={onRequestAgain}>
          Request approval again
        </Button>
      )}
    </section>
  );
}

function textChange(change: ChangeEvent): { field: string; from: string; to: string }[] {
  const out: { field: string; from: string; to: string }[] = [];
  for (const field of ["title", "description"]) {
    const value = change.data?.[field] as { from?: unknown; to?: unknown } | undefined;
    if (value && typeof value.to === "string") {
      out.push({ field, from: typeof value.from === "string" ? value.from : "", to: value.to });
    }
  }
  return out;
}

export function StaleApprovalPanel({
  item,
  stale,
  actions,
  names,
}: {
  item: ItemOut;
  stale: StaleApproval;
  actions: ItemActions;
  names: NameLookup;
}) {
  const [showing, setShowing] = useState(false);
  const canApprove = item.allowed_actions.includes("approve");

  return (
    <section
      aria-label="Changed since you opened it"
      role="alert"
      className="mt-5 rounded-panel border border-rule bg-p1-wash p-3 text-body"
    >
      <p className="font-strong">
        {item.key} changed since you opened it. Review the changes before approving.
      </p>
      {!canApprove && (
        <p className="mt-1">The approval no longer applies, so there is nothing to approve now.</p>
      )}
      <div className="mt-2 flex flex-wrap gap-2">
        <Button
          variant="secondary"
          className="h-8"
          onClick={() => {
            setShowing((v) => !v);
          }}
        >
          {showing ? "Hide changes" : "Show changes"}
        </Button>
        {canApprove && (
          <Button
            variant="primary"
            className="h-8"
            pending={actions.pending === "approve"}
            onClick={() => void actions.run(stale.input)}
          >
            Approve this version
          </Button>
        )}
        <Button variant="quiet" className="h-8" onClick={actions.dismissStale}>
          Dismiss
        </Button>
      </div>
      {showing && (
        <ul className="mt-3 flex flex-col gap-3 text-meta">
          {stale.changes.length === 0 && (
            <li>The server did not list what changed. Reload to see.</li>
          )}
          {stale.changes.map((change, i) => {
            const event = change as unknown as EventOut;
            return (
              <li key={i}>
                <p>
                  <span className="font-strong">{actorName(event)}</span>{" "}
                  {describeEvent(event, names)}
                  {event.created_at && (
                    <span className="text-pencil"> · {fullTime(event.created_at)}</span>
                  )}
                </p>
                {textChange(change).map((diff) => (
                  <div key={diff.field} className="mt-1">
                    <p className="mb-1 text-pencil">New {diff.field}, changes marked</p>
                    <DiffView before={diff.from} after={diff.to} />
                  </div>
                ))}
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
