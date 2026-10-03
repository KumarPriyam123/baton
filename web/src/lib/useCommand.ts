/**
 * The one way the UI changes anything (I6, I13, SPEC §6.2, §6.3, §6.5).
 *
 * - One Idempotency-Key per user action: created in `execute()`, carried in the mutation
 *   variables, so TanStack's automatic retries and any repeat submit of the same action reuse it.
 * - If-Match is read from the item cache when the mutation *runs*, not when it was queued, so a
 *   second command on the same item uses the version returned by the first.
 * - `scope: item:<id>` makes TanStack Query run one item's mutations one after another.
 * - Retries: only network errors and 503 BUSY, three times, backoff, same key.
 * - Success: merge the server's item (never older over newer), then refresh lists, facets, inbox.
 */
import {
  type QueryClient,
  type UseMutationResult,
  useMutation,
  useQueryClient,
} from "@tanstack/react-query";
import { useCallback } from "react";

import { keys } from "../api/keys";
import { ApiError, type ItemOut, isRetryable } from "./api";
import { notifyError } from "./errors";
import { getCachedItem, removeItem, upsertItem } from "./itemCache";

export const MAX_RETRIES = 3;

export interface CommandContext {
  /** Same value for every attempt of this user action. */
  idempotencyKey: string;
  /** `"<version>"` from the cached item; only set when the command needs If-Match. */
  ifMatch: string | undefined;
}

export interface CommandOptions<TInput, TResult> {
  /** The item the command changes. Omit for commands with no item yet (create). */
  item?: { id: string; key: string };
  /** SPEC §6.2 table: commands whose meaning depends on what the user saw. */
  needsIfMatch?: boolean;
  run: (input: TInput, ctx: CommandContext) => Promise<TResult>;
  /** The item inside the response, when the response isn't the item itself. */
  toItem?: (result: TResult) => ItemOut | undefined;
  /** Return true to say "handled": the default error toast is skipped. */
  onError?: (error: unknown) => boolean | undefined;
  retryDelay?: (attempt: number) => number;
}

interface Variables<TInput> {
  input: TInput;
  idempotencyKey: string;
}

export type Command<TInput, TResult> = UseMutationResult<TResult, Error, Variables<TInput>> & {
  /** Start the action. Pass `idempotencyKey` to make several submits of one form one action. */
  execute: (input: TInput, options?: { idempotencyKey?: string }) => Promise<TResult>;
};

export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

export function refreshAfterCommand(qc: QueryClient): void {
  void qc.invalidateQueries({ queryKey: keys.lists });
  void qc.invalidateQueries({ queryKey: ["items", "facets"] });
  void qc.invalidateQueries({ queryKey: keys.attention });
}

const defaultDelay = (attempt: number): number => Math.min(250 * 2 ** attempt, 2000);

export function useCommand<TInput, TResult>(
  options: CommandOptions<TInput, TResult>,
): Command<TInput, TResult> {
  const qc = useQueryClient();
  const { item, needsIfMatch, run, toItem, onError, retryDelay } = options;

  const mutation = useMutation<TResult, Error, Variables<TInput>>({
    ...(item ? { scope: { id: `item:${item.id}` } } : {}),
    mutationFn: ({ input, idempotencyKey }) => {
      let ifMatch: string | undefined;
      if (needsIfMatch) {
        const cached = item ? getCachedItem(qc, item.key) : undefined;
        if (!cached) throw new Error("Command needs If-Match but the item isn't in the cache.");
        ifMatch = `"${String(cached.version)}"`;
      }
      return run(input, { idempotencyKey, ifMatch });
    },
    retry: (failureCount, error) => failureCount < MAX_RETRIES && isRetryable(error),
    retryDelay: retryDelay ?? defaultDelay,
    onSuccess: (result) => {
      const incoming = toItem ? toItem(result) : (result as ItemOut | undefined);
      if (incoming && typeof incoming === "object" && "version" in incoming) {
        upsertItem(qc, incoming);
      }
      refreshAfterCommand(qc);
    },
    onError: (error) => {
      if (item && error instanceof ApiError && error.status === 404) removeItem(qc, item);
      if (onError?.(error) === true) return;
      notifyError(error);
    },
  });

  const execute = useCallback(
    (input: TInput, opts?: { idempotencyKey?: string }) =>
      mutation.mutateAsync({
        input,
        idempotencyKey: opts?.idempotencyKey ?? newIdempotencyKey(),
      }),
    // mutateAsync is stable across renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [mutation.mutateAsync],
  );

  return { ...mutation, execute };
}
