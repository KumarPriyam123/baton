/**
 * One property edit through `useSaveFields` (useCommand with If-Match and the 412 rebase of
 * SPEC §6.6). The caller says which fields; this says what happened, so each editor can show its
 * own conflict or reason step. A merge with someone else's different field is announced here.
 */
import { toast } from "sonner";

import { ApiError, type ItemOut } from "../../lib/api";
import { notifyError } from "../../lib/errors";
import { describeKept, fieldWord } from "../../lib/rebase";
import { type PatchFields, useSaveFields } from "./useSaveFields";

export type FieldEditResult =
  | { status: "saved" }
  /** Same field changed under you: the server's item is already in the cache. */
  | { status: "conflict"; authors: string[]; current: ItemOut | undefined }
  /** The server wants a reason (SPEC §4.2): ask for it and send again. */
  | { status: "reason" }
  | { status: "failed" };

export function useFieldEdit(item: ItemOut) {
  const { save, isPending } = useSaveFields(item);

  const edit = async (fields: PatchFields, saved?: string): Promise<FieldEditResult> => {
    try {
      const outcome = await save(fields);
      if (outcome.kind === "conflict") {
        return {
          status: "conflict",
          authors: outcome.plan.authors,
          current: outcome.error.current,
        };
      }
      toast.success(outcome.kept ? describeKept(outcome.kept) : (saved ?? "Saved"));
      return { status: "saved" };
    } catch (error) {
      if (error instanceof ApiError && error.code === "REASON_REQUIRED")
        return { status: "reason" };
      notifyError(error);
      return { status: "failed" };
    }
  };

  /** "Asha changed the type a moment ago. Yours wasn't saved." */
  const announceConflict = (
    result: Extract<FieldEditResult, { status: "conflict" }>,
    field: string,
  ) => {
    const who = result.authors[0] ?? "Someone";
    toast.error(`${who} changed the ${fieldWord(field)} a moment ago. Yours wasn't saved.`);
  };

  return { edit, announceConflict, isPending };
}
