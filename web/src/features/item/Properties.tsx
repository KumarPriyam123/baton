/**
 * Properties (DESIGN §4.4). Priority and watch are optimistic: the screen changes at once and
 * rolls back with a reason if the server says no (DESIGN §6.1). Everything else is read-only
 * here. A field that just changed under you fades from --dispatch-wash.
 */
import { Eye, EyeOff } from "lucide-react";
import { type ReactNode, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { keys } from "../../api/keys";
import { Avatar } from "../../components/ui/Avatar";
import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { Textarea } from "../../components/ui/Field";
import {
  MenuContent,
  MenuRadio,
  MenuRadioGroup,
  MenuRoot,
  MenuTrigger,
} from "../../components/ui/Menu";
import { PopoverAnchor, PopoverContent, PopoverRoot } from "../../components/ui/Popover";
import { PriorityGlyph } from "../../components/ui/PriorityGlyph";
import { StatusIcon } from "../../components/ui/Strip";
import { ApiError, type ItemOut, api, unwrap } from "../../lib/api";
import { notifyError } from "../../lib/errors";
import {
  PRIORITY_LABEL,
  RESOLUTION_LABEL,
  STATUS_LABEL,
  TYPE_LABEL,
  ageShort,
  fullTime,
} from "../../lib/format";
import { describeKept } from "../../lib/rebase";
import { useCommand } from "../../lib/useCommand";
import { useSaveFields } from "./useSaveFields";

/** "3 h" under two days, then days: the same units as the next-step chip. */
function overdueSpan(ms: number): string {
  const hours = Math.floor(ms / 3_600_000);
  if (hours < 1) return "under an hour";
  if (hours < 48) return `${String(hours)} h`;
  return `${String(Math.floor(hours / 24))} days`;
}

function dueText(item: ItemOut, now: number): { text: string; late: boolean } {
  if (!item.due_at) return { text: "No due date", late: false };
  const when = new Date(item.due_at).getTime();
  const open = item.status !== "resolved" && item.status !== "closed";
  if (open && when < now) {
    return { text: `Overdue by ${overdueSpan(now - when)}`, late: true };
  }
  return { text: fullTime(item.due_at), late: false };
}

export function usePriorityEdit(item: ItemOut) {
  const qc = useQueryClient();
  const { save } = useSaveFields(item);
  const [reasonFor, setReasonFor] = useState<number | null>(null);

  const change = async (next: number, reason?: string): Promise<boolean> => {
    const key = keys.item(item.key);
    const before = qc.getQueryData<ItemOut>(key);
    const optimistic = before ? { ...before, priority: next } : undefined;
    if (optimistic) qc.setQueryData(key, optimistic);
    // Roll back only if nothing newer has arrived since (a 412 merges the server's item).
    const rollback = () => {
      if (before && qc.getQueryData(key) === optimistic) qc.setQueryData(key, before);
    };
    try {
      const outcome = await save({ priority: next, ...(reason ? { reason } : {}) });
      if (outcome.kind === "conflict") {
        rollback();
        const who = outcome.plan.authors[0] ?? "Someone";
        toast.error(`${who} changed the priority a moment ago. Yours wasn't saved.`);
        return false;
      }
      if (outcome.kept) toast.success(describeKept(outcome.kept));
      setReasonFor(null);
      return true;
    } catch (error) {
      rollback();
      if (error instanceof ApiError && error.code === "REASON_REQUIRED") {
        setReasonFor(next); // the server decides when a reason is needed; we just ask for it
        return false;
      }
      notifyError(error);
      return false;
    }
  };

  return {
    change,
    reasonFor,
    cancelReason: () => {
      setReasonFor(null);
    },
  };
}

export function useWatch(item: ItemOut) {
  const qc = useQueryClient();
  const command = useCommand<boolean, ItemOut>({
    item,
    run: (watch) =>
      unwrap(
        watch
          ? api.PUT("/api/v1/items/{key}/watch", { params: { path: { key: item.key } } })
          : api.DELETE("/api/v1/items/{key}/watch", { params: { path: { key: item.key } } }),
      ),
  });
  const toggle = async () => {
    const key = keys.item(item.key);
    const before = qc.getQueryData<ItemOut>(key);
    if (!before) return;
    const optimistic = { ...before, watching: !before.watching };
    qc.setQueryData(key, optimistic);
    try {
      await command.execute(optimistic.watching);
    } catch {
      if (qc.getQueryData(key) === optimistic) qc.setQueryData(key, before); // toast came from useCommand
    }
  };
  return { toggle };
}

function Row({
  label,
  children,
  changed,
}: {
  label: string;
  children: ReactNode;
  changed?: boolean;
}) {
  return (
    <>
      <dt className="text-pencil">{label}</dt>
      <dd className={cn("flex min-h-6 items-center gap-2 rounded-chip", changed && "wash")}>
        {children}
      </dd>
    </>
  );
}

export function Properties({
  item,
  now,
  changedFields,
}: {
  item: ItemOut;
  now: number;
  /** Fields that changed live since the last render; they get the wash highlight. */
  changedFields: ReadonlySet<string>;
}) {
  const priority = usePriorityEdit(item);
  const watch = useWatch(item);
  const canChangePriority = item.allowed_actions.includes("change_priority");
  const canWatch = item.allowed_actions.includes("watch");
  const due = dueText(item, now);
  const [reason, setReason] = useState("");

  return (
    <section aria-labelledby="props-heading" className="mt-8">
      <h2 id="props-heading" className="mb-3 text-section">
        Properties
      </h2>
      <dl className="grid grid-cols-[112px_minmax(0,1fr)] items-center gap-x-4 gap-y-2.5 text-body">
        <Row label="Status" changed={changedFields.has("status")}>
          <StatusIcon status={item.status} />
          {STATUS_LABEL[item.status]}
        </Row>

        <Row label="Priority" changed={changedFields.has("priority")}>
          <PopoverRoot
            open={priority.reasonFor !== null}
            onOpenChange={(open) => {
              if (!open) {
                priority.cancelReason();
                setReason("");
              }
            }}
          >
            <PopoverAnchor asChild>
              <span className="inline-flex">
                {canChangePriority ? (
                  <MenuRoot>
                    <MenuTrigger asChild>
                      <button
                        type="button"
                        aria-label={`Priority ${PRIORITY_LABEL[item.priority] ?? ""}, change`}
                        className="-ml-1 cursor-pointer rounded-chip px-1 hover:bg-dispatch-wash"
                      >
                        <PriorityGlyph priority={item.priority} />
                      </button>
                    </MenuTrigger>
                    <MenuContent>
                      <MenuRadioGroup
                        value={String(item.priority)}
                        onValueChange={(value) => void priority.change(Number(value))}
                      >
                        {PRIORITY_LABEL.map((label, index) => (
                          <MenuRadio key={label} value={String(index)}>
                            <PriorityGlyph priority={index} />
                          </MenuRadio>
                        ))}
                      </MenuRadioGroup>
                    </MenuContent>
                  </MenuRoot>
                ) : (
                  <PriorityGlyph priority={item.priority} />
                )}
              </span>
            </PopoverAnchor>
            {priority.reasonFor !== null && (
              <PopoverContent label="Reason for lowering the priority">
                <form
                  className="flex flex-col gap-3"
                  onSubmit={(event) => {
                    event.preventDefault();
                    if (priority.reasonFor !== null && reason.trim() !== "") {
                      void priority.change(priority.reasonFor, reason.trim()).then((ok) => {
                        if (ok) setReason("");
                      });
                    }
                  }}
                >
                  <h3 className="text-section">
                    Change priority to {PRIORITY_LABEL[priority.reasonFor] ?? ""}
                  </h3>
                  <label
                    className="flex flex-col gap-1 text-meta font-strong"
                    htmlFor="prio-reason"
                  >
                    Why is it changing?
                    <Textarea
                      id="prio-reason"
                      value={reason}
                      className="min-h-20 font-body"
                      onChange={(e) => {
                        setReason(e.target.value);
                      }}
                    />
                  </label>
                  <div className="flex justify-end gap-2">
                    <Button variant="quiet" onClick={priority.cancelReason}>
                      Cancel
                    </Button>
                    <Button type="submit" variant="primary" disabled={reason.trim() === ""}>
                      Change priority
                    </Button>
                  </div>
                </form>
              </PopoverContent>
            )}
          </PopoverRoot>
        </Row>

        <Row label="Owner" changed={changedFields.has("assignee")}>
          <Avatar person={item.assignee} />
          {item.assignee?.name ?? "Nobody yet"}
        </Row>
        <Row label="Team" changed={changedFields.has("team")}>
          {item.team.name}
        </Row>
        <Row label="Requester">
          <Avatar person={item.requester} />
          {item.requester.name}
        </Row>
        <Row label="Due" changed={changedFields.has("due_at")}>
          <span className={cn("tnum", due.late && "font-strong text-p0")}>{due.text}</span>
        </Row>
        <Row label="Type" changed={changedFields.has("type")}>
          {TYPE_LABEL[item.type]}
        </Row>
        <Row label="Needs approval">{item.requires_approval ? "Yes" : "No"}</Row>
        {item.resolution && <Row label="Resolution">{RESOLUTION_LABEL[item.resolution]}</Row>}
        {canWatch && (
          <Row label="Watching">
            <Button
              variant="quiet"
              className="-ml-2 h-7 px-2 text-meta"
              aria-pressed={item.watching}
              onClick={() => void watch.toggle()}
            >
              {item.watching ? (
                <Eye className="size-4" strokeWidth={1.75} aria-hidden />
              ) : (
                <EyeOff className="size-4" strokeWidth={1.75} aria-hidden />
              )}
              {item.watching ? "Watching" : "Watch"}
            </Button>
          </Row>
        )}
        <Row label="Created">
          <span className="tnum" title={fullTime(item.created_at)}>
            {ageShort(item.created_at, now)} ago
          </span>
        </Row>
        <Row label="Updated">
          <span className="tnum" title={fullTime(item.updated_at)}>
            {ageShort(item.updated_at, now)} ago
          </span>
        </Row>
      </dl>
    </section>
  );
}
