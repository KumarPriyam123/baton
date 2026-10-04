/**
 * What the command palette offers for the open item, as data (DESIGN §4.12). Like the action bar,
 * it knows no status and no role: every entry exists because the server put it in `allowed_actions`
 * (I4). Actions that need more than a reason or a person (close, move to team) stay in the action
 * bar; the palette doesn't try to host those forms.
 */
import { ACTIONS, type ActionSpec, barModel } from "../item/actions";
import type { ItemOut } from "../../lib/api";
import { PRIORITY_LABEL } from "../../lib/format";

export const KEY_PATTERN = /^[A-Za-z]{2,5}-\d+$/;

/** Forms the palette can collect in its own second step. */
const PALETTE_FORMS = new Set(["reason", "assign"]);

export function paletteActionSpecs(item: ItemOut): ActionSpec[] {
  const model = barModel(item.allowed_actions, item.next_step);
  const ordered = [...(model.primary ? [model.primary] : []), ...model.beside, ...model.more];
  return ordered.filter(
    (spec) =>
      ACTIONS[spec.action] !== undefined && (!spec.form || PALETTE_FORMS.has(spec.form.kind)),
  );
}

export interface PriorityChoice {
  value: number;
  label: string;
}

/** "Set priority P1" for every level except the current one, only when the server allows it. */
export function priorityChoices(item: ItemOut): PriorityChoice[] {
  if (!item.allowed_actions.includes("change_priority")) return [];
  return PRIORITY_LABEL.map((label, value) => ({ value, label: `Set priority ${label}` })).filter(
    (choice) => choice.value !== item.priority,
  );
}

export function canWatch(item: ItemOut): boolean {
  return item.allowed_actions.includes("watch");
}

/** The item key in a `/items/PAY-142` path, if the user is looking at one. */
export function openItemKey(pathname: string): string | undefined {
  const match = /^\/items\/([^/]+)/.exec(pathname);
  return match?.[1] ? decodeURIComponent(match[1]) : undefined;
}
