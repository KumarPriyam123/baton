/**
 * The action bar (DESIGN §4.4). Buttons come ONLY from `item.allowed_actions` (I4): the server
 * decides what is possible, this component decides how it looks. The primary button follows
 * `next_step`; the rest sit beside it or in "More". Actions that need words open one small
 * popover, anchored to the bar.
 */
import { ChevronDown } from "lucide-react";
import { useState } from "react";

import { useTeams } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { Input, Textarea } from "../../components/ui/Field";
import { Field } from "../../components/ui/Field";
import { MenuContent, MenuItem, MenuRoot, MenuTrigger } from "../../components/ui/Menu";
import { PopoverAnchor, PopoverContent, PopoverRoot } from "../../components/ui/Popover";
import type { components } from "../../api/generated";
import type { ItemOut } from "../../lib/api";
import { type ActionSpec, barModel } from "./actions";
import type { Person } from "./track";
import type { ActionInput, ItemActions } from "./useItemActions";

type Resolution = components["schemas"]["Resolution"];

const RESOLUTIONS: { value: Resolution; label: string }[] = [
  { value: "done", label: "Done" },
  { value: "duplicate", label: "Duplicate" },
  { value: "wont_do", label: "Won't do" },
  { value: "cannot_reproduce", label: "Can't reproduce" },
];

/** Actions with no form: one click sends them. */
export function simpleInput(spec: ActionSpec, item: ItemOut): ActionInput | null {
  switch (spec.action) {
    case "claim":
    case "release":
      return { action: spec.action };
    case "unblock":
      return { action: "unblock" };
    case "approve":
      return item.approval ? { action: "approve", approvalId: item.approval.id } : null;
    case "cancel_approval":
      return item.approval ? { action: "cancel_approval", approvalId: item.approval.id } : null;
    default:
      return null;
  }
}

export function ActionBar({
  item,
  actions,
  members,
}: {
  item: ItemOut;
  actions: ItemActions;
  members: readonly Person[];
}) {
  const model = barModel(item.allowed_actions, item.next_step);
  const [form, setForm] = useState<ActionSpec | null>(null);
  const busy = actions.pending !== null;

  const choose = (spec: ActionSpec) => {
    if (spec.form) {
      setForm(spec);
      return;
    }
    const input = simpleInput(spec, item);
    if (input) void actions.run(input);
  };

  const requestApproval =
    item.allowed_actions.includes("request_approval") && actions.needsApproval;

  if (!model.primary && model.more.length === 0) {
    return (
      <p className="mt-4 text-meta text-pencil" data-testid="no-actions">
        You can comment on this request, but there's nothing else for you to do on it.
      </p>
    );
  }

  const button = (spec: ActionSpec, variant: "primary" | "secondary") => (
    <Button
      key={spec.action}
      variant={spec.danger && variant === "secondary" ? "secondary" : variant}
      pending={actions.pending === spec.action}
      disabled={busy && actions.pending !== spec.action}
      onClick={() => {
        choose(spec);
      }}
    >
      {spec.label}
    </Button>
  );

  return (
    <div className="mt-5">
      <PopoverRoot
        open={form !== null}
        onOpenChange={(open) => {
          if (!open) setForm(null);
        }}
      >
        <PopoverAnchor asChild>
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Actions">
            {model.primary && button(model.primary, "primary")}
            {model.beside.map((spec) => button(spec, "secondary"))}
            {model.more.length > 0 && (
              <MenuRoot>
                <MenuTrigger asChild>
                  <Button variant="secondary" disabled={busy}>
                    More
                    <ChevronDown className="size-4" strokeWidth={1.75} aria-hidden />
                  </Button>
                </MenuTrigger>
                <MenuContent>
                  {model.more.map((spec) => (
                    <MenuItem
                      key={spec.action}
                      onSelect={() => {
                        choose(spec);
                      }}
                    >
                      {spec.label}
                    </MenuItem>
                  ))}
                </MenuContent>
              </MenuRoot>
            )}
          </div>
        </PopoverAnchor>
        {form?.form && (
          <PopoverContent label={form.form.title}>
            <ActionForm
              key={form.action}
              spec={form}
              item={item}
              members={members}
              pending={actions.pending === form.action}
              onSubmit={async (input) => {
                if (await actions.run(input)) setForm(null);
              }}
              onCancel={() => {
                setForm(null);
              }}
            />
          </PopoverContent>
        )}
      </PopoverRoot>
      {requestApproval && (
        <p className="mt-3 flex flex-wrap items-center gap-2 text-meta text-pencil">
          This needs an approval before it can be resolved.
          <Button
            variant="secondary"
            className="h-7 px-2 text-meta"
            onClick={() => {
              const spec = model.more.find((s) => s.action === "request_approval") ?? model.primary;
              if (spec?.action === "request_approval") setForm(spec);
            }}
          >
            Request approval
          </Button>
        </p>
      )}
    </div>
  );
}

function ActionForm({
  spec,
  item,
  members,
  pending,
  onSubmit,
  onCancel,
}: {
  spec: ActionSpec;
  item: ItemOut;
  members: readonly Person[];
  pending: boolean;
  onSubmit: (input: ActionInput) => Promise<void>;
  onCancel: () => void;
}) {
  const teams = useTeams();
  const config = spec.form;
  const [reason, setReason] = useState("");
  const [resolution, setResolution] = useState<Resolution>("done");
  const [duplicateOf, setDuplicateOf] = useState("");
  const [teamKey, setTeamKey] = useState("");
  const [assignee, setAssignee] = useState("");
  if (!config) return null;

  const closing = config.kind === "close";
  const resolvedAlready = item.status === "resolved";
  const needsReason = config.kind === "close" ? !resolvedAlready : (config.reasonRequired ?? false);
  const targetTeams = (teams.data ?? []).filter((t) => t.key !== item.team.key);

  const missing =
    (needsReason && reason.trim() === "") ||
    (config.kind === "transfer" && (teamKey === "" || reason.trim() === "")) ||
    (config.kind === "assign" && assignee === "") ||
    (closing && !resolvedAlready && resolution === "duplicate" && duplicateOf.trim() === "");

  const build = (): ActionInput | null => {
    const text = reason.trim() === "" ? undefined : reason.trim();
    switch (spec.action) {
      case "assign": {
        const person = members.find((m) => m.id === assignee);
        return person ? { action: "assign", assigneeId: person.id, name: person.name } : null;
      }
      case "unassign":
        return text ? { action: "unassign", reason: text } : { action: "unassign" };
      case "close":
        return {
          action: "close",
          ...(text ? { reason: text } : {}),
          ...(resolvedAlready ? {} : { resolution }),
          ...(resolution === "duplicate" && !resolvedAlready
            ? { duplicateOf: duplicateOf.trim().toUpperCase() }
            : {}),
        };
      case "transfer": {
        const team = targetTeams.find((t) => t.key === teamKey);
        return team
          ? { action: "transfer", teamKey, teamName: team.name, reason: reason.trim() }
          : null;
      }
      case "request_approval":
        return text ? { action: "request_approval", reason: text } : { action: "request_approval" };
      case "reject":
        return item.approval
          ? { action: "reject", approvalId: item.approval.id, ...(text ? { reason: text } : {}) }
          : null;
      case "block":
      case "resolve":
      case "reopen":
      case "withdraw":
        return text ? { action: spec.action, reason: text } : { action: spec.action };
      default:
        return null;
    }
  };

  return (
    <form
      className="flex flex-col gap-3"
      onSubmit={(event) => {
        event.preventDefault();
        const input = build();
        if (input && !missing) void onSubmit(input);
      }}
    >
      <h3 className="text-section">{config.title}</h3>

      {config.kind === "assign" && (
        <Field label="Person" htmlFor="af-assignee">
          <select
            id="af-assignee"
            value={assignee}
            onChange={(e) => {
              setAssignee(e.target.value);
            }}
            className="h-9 w-full rounded-chip border border-rule bg-sheet px-2 text-body"
          >
            <option value="">Choose a person</option>
            {members.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      {config.kind === "transfer" && (
        <Field label="Team" htmlFor="af-team">
          <select
            id="af-team"
            value={teamKey}
            onChange={(e) => {
              setTeamKey(e.target.value);
            }}
            className="h-9 w-full rounded-chip border border-rule bg-sheet px-2 text-body"
          >
            <option value="">Choose a team</option>
            {targetTeams.map((t) => (
              <option key={t.key} value={t.key}>
                {t.name}
              </option>
            ))}
          </select>
        </Field>
      )}

      {closing && !resolvedAlready && (
        <Field label="Resolution" htmlFor="af-resolution">
          <select
            id="af-resolution"
            value={resolution}
            onChange={(e) => {
              setResolution(e.target.value as Resolution);
            }}
            className="h-9 w-full rounded-chip border border-rule bg-sheet px-2 text-body"
          >
            {RESOLUTIONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
      )}

      {closing && !resolvedAlready && resolution === "duplicate" && (
        <Field
          label="Duplicate of"
          htmlFor="af-duplicate"
          hint="The key of the request it repeats."
        >
          <Input
            id="af-duplicate"
            value={duplicateOf}
            placeholder="PAY-131"
            onChange={(e) => {
              setDuplicateOf(e.target.value);
            }}
          />
        </Field>
      )}

      {config.kind !== "assign" && (
        <Field
          label={
            config.reasonLabel ??
            (config.kind === "transfer"
              ? "Why is it moving?"
              : closing
                ? resolvedAlready
                  ? "Note (optional)"
                  : "Why is it being closed?"
                : "Reason")
          }
          htmlFor="af-reason"
        >
          <Textarea
            id="af-reason"
            value={reason}
            placeholder={config.placeholder}
            className="min-h-20"
            onChange={(e) => {
              setReason(e.target.value);
            }}
          />
        </Field>
      )}

      <div className="flex justify-end gap-2">
        <Button variant="quiet" onClick={onCancel}>
          Cancel
        </Button>
        <Button
          type="submit"
          variant={spec.danger ? "danger" : "primary"}
          pending={pending}
          disabled={missing}
        >
          {config.submit}
        </Button>
      </div>
    </form>
  );
}
