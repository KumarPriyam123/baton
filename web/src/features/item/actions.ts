/**
 * What the action bar offers, as data. The server decides WHICH actions exist (`allowed_actions`,
 * I4); this file only says how each one is labelled and which input it collects first.
 * Nothing here knows a status, a role or a transition.
 */
import type { components } from "../../api/generated";

export type Action = components["schemas"]["Action"];
export type NextStep = components["schemas"]["NextStepOut"];

/** The input a form collects before the action is sent. */
export type FormKind =
  | "reason" // one text box
  | "close" // resolution + reason (+ duplicate key)
  | "transfer" // team + reason
  | "assign"; // person

export interface ActionSpec {
  action: Action;
  label: string;
  /** Opens a small popover first. Absent: the click sends the request. */
  form?: {
    kind: FormKind;
    title: string;
    submit: string;
    reasonLabel?: string;
    /** UX hint only: the server enforces it (REASON_REQUIRED) and its message is shown. */
    reasonRequired?: boolean;
    placeholder?: string;
  };
  danger?: boolean;
}

export const ACTIONS: Partial<Record<Action, ActionSpec>> = {
  claim: { action: "claim", label: "Assign to me" },
  release: { action: "release", label: "Release" },
  assign: {
    action: "assign",
    label: "Assign to…",
    form: { kind: "assign", title: "Assign to", submit: "Assign" },
  },
  unassign: {
    action: "unassign",
    label: "Unassign",
    form: {
      kind: "reason",
      title: "Unassign",
      submit: "Unassign",
      reasonLabel: "Note (optional)",
      reasonRequired: false,
    },
  },
  block: {
    action: "block",
    label: "Block",
    form: {
      kind: "reason",
      title: "Mark as blocked",
      submit: "Mark blocked",
      reasonLabel: "What is it waiting on?",
      reasonRequired: true,
      placeholder: "Waiting on a response from the bank",
    },
  },
  unblock: { action: "unblock", label: "Unblock" },
  resolve: {
    action: "resolve",
    label: "Resolve",
    form: {
      kind: "reason",
      title: "Resolve",
      submit: "Resolve",
      reasonLabel: "What was done?",
      reasonRequired: true,
      placeholder: "Refund re-issued and confirmed with the customer",
    },
  },
  reopen: {
    action: "reopen",
    label: "Reopen",
    form: {
      kind: "reason",
      title: "Reopen",
      submit: "Reopen",
      reasonLabel: "Why is it being reopened?",
      reasonRequired: true,
    },
  },
  close: {
    action: "close",
    label: "Close",
    form: { kind: "close", title: "Close", submit: "Close request" },
  },
  withdraw: {
    action: "withdraw",
    label: "Withdraw",
    danger: true,
    form: {
      kind: "reason",
      title: "Withdraw this request",
      submit: "Withdraw",
      reasonLabel: "Why are you withdrawing it?",
      reasonRequired: true,
    },
  },
  transfer: {
    action: "transfer",
    label: "Move to team…",
    form: { kind: "transfer", title: "Move to another team", submit: "Move" },
  },
  request_approval: {
    action: "request_approval",
    label: "Request approval",
    form: {
      kind: "reason",
      title: "Request approval",
      submit: "Request approval",
      reasonLabel: "What should the approver look at? (optional)",
      reasonRequired: false,
    },
  },
  approve: { action: "approve", label: "Approve" },
  reject: {
    action: "reject",
    label: "Reject",
    danger: true,
    form: {
      kind: "reason",
      title: "Reject",
      submit: "Reject",
      reasonLabel: "Why is it being rejected?",
      reasonRequired: true,
    },
  },
  cancel_approval: { action: "cancel_approval", label: "Cancel approval request" },
};

/** Shown in the bar in this order. Everything else on `allowed_actions` is not a button here. */
export const BAR_ORDER: readonly Action[] = [
  "claim",
  "approve",
  "reject",
  "resolve",
  "request_approval",
  "unblock",
  "block",
  "reopen",
  "close",
  "assign",
  "unassign",
  "release",
  "transfer",
  "cancel_approval",
  "withdraw",
];

/**
 * The primary button follows `next_step` (DESIGN §4.4): unowned → Assign to me; awaiting your
 * approval → Approve; yours and in progress → Resolve. The first preference the server allows wins.
 */
const PREFERRED: Record<string, readonly Action[]> = {
  approval: ["approve"],
  needs_owner: ["claim", "assign"],
  blocked: ["unblock", "resolve"],
  resolved: ["close", "reopen"],
  approval_wait: ["cancel_approval"],
};
const FALLBACK: readonly Action[] = ["resolve", "claim", "unblock", "request_approval", "reopen"];

export function pickPrimary(
  allowed: readonly Action[],
  nextStep: Pick<NextStep, "kind">,
): Action | undefined {
  const candidates = [...(PREFERRED[nextStep.kind] ?? []), ...FALLBACK];
  return candidates.find((action) => allowed.includes(action) && ACTIONS[action]);
}

export interface BarModel {
  primary: ActionSpec | undefined;
  /** Shown next to the primary (Reject beside Approve). */
  beside: ActionSpec[];
  more: ActionSpec[];
}

export function barModel(allowed: readonly Action[], nextStep: Pick<NextStep, "kind">): BarModel {
  const primaryAction = pickPrimary(allowed, nextStep);
  const primary = primaryAction ? ACTIONS[primaryAction] : undefined;
  const rest = BAR_ORDER.filter((a) => allowed.includes(a) && a !== primaryAction)
    .map((a) => ACTIONS[a])
    .filter((spec): spec is ActionSpec => spec !== undefined);
  const beside = primaryAction === "approve" ? rest.filter((s) => s.action === "reject") : [];
  return { primary, beside, more: rest.filter((s) => !beside.includes(s)) };
}
