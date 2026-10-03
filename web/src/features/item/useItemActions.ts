/**
 * Every workflow action on one item, through one `useCommand` (I6, I13): one scope per item so
 * two quick clicks run one after the other, each on the version the previous one returned.
 *
 * Confirmed, not optimistic (DESIGN §6.1): the button shows a spinner and the other actions wait
 * until the server has decided. A lost claim race says who won (409 ALREADY_CLAIMED).
 */
import { useRef, useState } from "react";
import { toast } from "sonner";

import type { components } from "../../api/generated";
import { ApiError, type ItemOut, api, unwrap } from "../../lib/api";
import { describeClaimLost, describeError } from "../../lib/errors";
import type { ChangeEvent } from "../../lib/rebase";
import { type CommandContext, useCommand } from "../../lib/useCommand";
import type { Action } from "./actions";

type Resolution = components["schemas"]["Resolution"];
type TransitionAction = components["schemas"]["TransitionRequest"]["action"];

export type ActionInput =
  | { action: "claim" }
  | { action: "release" }
  | { action: "assign"; assigneeId: string; name: string }
  | { action: "unassign"; reason?: string }
  | {
      action: Extract<TransitionAction, "block" | "unblock" | "resolve" | "reopen" | "withdraw">;
      reason?: string;
    }
  | { action: "close"; reason?: string; resolution?: Resolution; duplicateOf?: string }
  | { action: "transfer"; teamKey: string; teamName: string; reason: string }
  | { action: "request_approval"; reason?: string }
  | { action: "approve"; approvalId: string }
  | { action: "reject"; approvalId: string; reason?: string }
  | { action: "cancel_approval"; approvalId: string };

/** A 412 on approve: the item changed after the approver opened it (SPEC 4.4). */
export interface StaleApproval {
  input: Extract<ActionInput, { action: "approve" }>;
  changes: ChangeEvent[];
}

const SUCCESS: Partial<Record<Action, (input: ActionInput) => string>> = {
  claim: () => "Assigned to you",
  release: () => "Released",
  assign: (i) => (i.action === "assign" ? `Assigned to ${i.name}` : "Assigned"),
  unassign: () => "Unassigned",
  block: () => "Marked blocked",
  unblock: () => "Unblocked",
  resolve: () => "Resolved",
  reopen: () => "Reopened",
  close: () => "Closed",
  withdraw: () => "Withdrawn",
  transfer: (i) => (i.action === "transfer" ? `Moved to ${i.teamName}` : "Moved"),
  request_approval: () => "Approval requested",
  approve: () => "Approved",
  reject: () => "Rejected",
  cancel_approval: () => "Approval request cancelled",
};

const SKIP_IF_MATCH = new Set<Action>(["claim", "release", "cancel_approval"]);

async function send(item: ItemOut, input: ActionInput, ctx: CommandContext): Promise<ItemOut> {
  const path = { key: item.key };
  const header = {
    "Idempotency-Key": ctx.idempotencyKey,
    "If-Match": SKIP_IF_MATCH.has(input.action) ? null : (ctx.ifMatch ?? null),
  };
  switch (input.action) {
    case "claim":
      return unwrap(api.POST("/api/v1/items/{key}/claim", { params: { path, header } }));
    case "release":
      return unwrap(api.POST("/api/v1/items/{key}/release", { params: { path, header } }));
    case "assign":
      return unwrap(
        api.POST("/api/v1/items/{key}/assign", {
          params: { path, header },
          body: { assignee_id: input.assigneeId },
        }),
      );
    case "unassign":
      return unwrap(
        api.POST("/api/v1/items/{key}/assign", {
          params: { path, header },
          body: { assignee_id: null, reason: input.reason ?? null },
        }),
      );
    case "block":
    case "unblock":
    case "resolve":
    case "reopen":
    case "withdraw":
    case "close":
      return unwrap(
        api.POST("/api/v1/items/{key}/transition", {
          params: { path, header },
          body: {
            action: input.action,
            reason: input.reason ?? null,
            ...(input.action === "close"
              ? { resolution: input.resolution ?? null, duplicate_of: input.duplicateOf ?? null }
              : {}),
          },
        }),
      );
    case "transfer":
      return unwrap(
        api.POST("/api/v1/items/{key}/transfer", {
          params: { path, header },
          body: { team_key: input.teamKey, reason: input.reason },
        }),
      );
    case "request_approval":
      return unwrap(
        api.POST("/api/v1/items/{key}/approvals", {
          params: { path, header },
          body: { note: input.reason ?? null },
        }),
      );
    case "approve":
    case "reject":
      return unwrap(
        api.POST("/api/v1/items/{key}/approvals/{approval_id}/decision", {
          params: { path: { ...path, approval_id: input.approvalId }, header },
          body: {
            decision: input.action,
            note: input.action === "reject" ? (input.reason ?? null) : null,
          },
        }),
      );
    case "cancel_approval":
      return unwrap(
        api.POST("/api/v1/items/{key}/approvals/{approval_id}/cancel", {
          params: { path: { ...path, approval_id: input.approvalId }, header },
        }),
      );
  }
}

export interface ItemActions {
  /** Resolves true when the server accepted it. Errors are explained by a toast already. */
  run: (input: ActionInput) => Promise<boolean>;
  /** The action in flight, so its button can spin and the others wait. */
  pending: Action | null;
  stale: StaleApproval | null;
  dismissStale: () => void;
  /** Set when resolve answered APPROVAL_REQUIRED: the bar offers "Request approval". */
  needsApproval: boolean;
}

export function useItemActions(item: ItemOut): ItemActions {
  const [pending, setPending] = useState<Action | null>(null);
  const [stale, setStale] = useState<StaleApproval | null>(null);
  const [needsApproval, setNeedsApproval] = useState(false);
  const inFlight = useRef<Action | null>(null);

  const command = useCommand<ActionInput, ItemOut>({
    item,
    needsIfMatch: true,
    run: (input, ctx) => send(item, input, ctx),
    onError: (error) => {
      if (!(error instanceof ApiError)) return undefined;
      if (error.code === "ALREADY_CLAIMED") {
        toast.error(describeClaimLost(error, item.key));
        return true;
      }
      if (error.code === "APPROVAL_REQUIRED") {
        setNeedsApproval(true);
        toast.error(describeError(error));
        return true;
      }
      // A stale approve is explained by the review panel, not a toast.
      if (error.status === 412 && inFlight.current === "approve") return true;
      if (error.status === 412) {
        toast.error(`${item.key} changed since you opened it. Check the latest and try again.`);
        return true;
      }
      return undefined;
    },
  });

  const run = async (input: ActionInput): Promise<boolean> => {
    setPending(input.action);
    inFlight.current = input.action;
    try {
      await command.execute(input);
      setStale(null);
      setNeedsApproval(false);
      toast.success(SUCCESS[input.action]?.(input) ?? "Done");
      return true;
    } catch (error) {
      if (error instanceof ApiError && error.status === 412 && input.action === "approve") {
        // useCommand has already merged `current` into the cache; the user reviews, then re-approves.
        setStale({ input, changes: error.changesSince ?? [] });
      }
      return false;
    } finally {
      inFlight.current = null;
      setPending(null);
    }
  };

  return {
    run,
    pending,
    stale,
    dismissStale: () => {
      setStale(null);
    },
    needsApproval,
  };
}
