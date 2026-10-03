/**
 * PATCH an item's fields with the 412 rules of SPEC §6.6 (lib/rebase.ts). Used by the description
 * editor and the priority picker. Every send, including each rebase, carries a new
 * Idempotency-Key (decision 34); `version` is what the caller *saw*, so an edit made on an old
 * view can't silently overwrite a newer one.
 */
import type { components } from "../../api/generated";
import { ApiError, type ItemOut, api, unwrap } from "../../lib/api";
import { type RebaseOutcome, runWithRebase } from "../../lib/rebase";
import { getCachedItem } from "../../lib/itemCache";
import { useCommand } from "../../lib/useCommand";
import { useQueryClient } from "@tanstack/react-query";

export type PatchFields = Partial<
  Omit<components["schemas"]["PatchItemRequest"], "apply_type_defaults">
>;

interface PatchInput {
  fields: PatchFields;
  version: number;
}

export function useSaveFields(item: ItemOut) {
  const qc = useQueryClient();
  const command = useCommand<PatchInput, ItemOut>({
    item,
    run: ({ fields, version }, ctx) =>
      unwrap(
        api.PATCH("/api/v1/items/{key}", {
          params: {
            path: { key: item.key },
            header: { "Idempotency-Key": ctx.idempotencyKey, "If-Match": `"${String(version)}"` },
          },
          body: { ...fields, apply_type_defaults: false },
        }),
      ),
    // 412 is handled by the rebase; REASON_REQUIRED by the caller's popover.
    onError: (error) =>
      error instanceof ApiError && (error.status === 412 || error.code === "REASON_REQUIRED"),
  });

  /** `version` defaults to the cached item's: the view the user is looking at. */
  const save = (fields: PatchFields, version?: number): Promise<RebaseOutcome<ItemOut>> => {
    const start = version ?? getCachedItem(qc, item.key)?.version ?? item.version;
    const mine = Object.keys(fields).filter((f) => f !== "reason");
    return runWithRebase(mine, start, (v) => command.execute({ fields, version: v }));
  };

  return { save, isPending: command.isPending };
}
