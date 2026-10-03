/**
 * Description (DESIGN §4.4, §6.2): rendered Markdown; editing switches to a textarea with a
 * Preview tab and keeps a local draft.
 *
 * - The draft remembers the version and text it started from. Saving sends that version in
 *   `If-Match`, so an edit made on an old view hits 412 instead of silently overwriting (SPEC §6.2).
 * - 412 with other fields changed: lib/rebase.ts resends on the new version and we say so.
 *   Same field changed: ConflictDialog.
 * - If the description changes on the server while a draft is open, a banner says so and the
 *   draft stays.
 */
import { Pencil } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { DiffView } from "../../components/ui/DiffView";
import { Textarea } from "../../components/ui/Field";
import { Markdown } from "../../components/ui/Markdown";
import { announce } from "../../lib/announce";
import type { ItemOut } from "../../lib/api";
import { describeError } from "../../lib/errors";
import { describeKept } from "../../lib/rebase";
import { ConflictDialog } from "./ConflictDialog";
import { useSaveFields } from "./useSaveFields";

const MAX = 20_000;

interface Draft {
  text: string;
  /** The version and description the user started editing from. */
  baseVersion: number;
  baseText: string;
}

const storageKey = (id: string) => `baton:draft:description:${id}`;

function readDraft(id: string): Draft | null {
  try {
    const raw = window.localStorage.getItem(storageKey(id));
    return raw ? (JSON.parse(raw) as Draft) : null;
  } catch {
    return null;
  }
}
function writeDraft(id: string, draft: Draft | null): void {
  try {
    if (draft) window.localStorage.setItem(storageKey(id), JSON.stringify(draft));
    else window.localStorage.removeItem(storageKey(id));
  } catch {
    // Private window or blocked storage: the draft just lives in memory.
  }
}

export function Description({ item }: { item: ItemOut }) {
  const canEdit = item.allowed_actions.includes("edit_text");
  const [draft, setDraft] = useState<Draft | null>(() => readDraft(item.id));
  const [tab, setTab] = useState<"write" | "preview">("write");
  const [reviewing, setReviewing] = useState(false);
  const [bannerDismissedFor, setBannerDismissedFor] = useState<string | null>(null);
  const [conflict, setConflict] = useState<{
    savedNow: string;
    version: number;
    authors: string[];
  } | null>(null);
  const { save, isPending } = useSaveFields(item);
  const editor = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    writeDraft(item.id, draft);
  }, [draft, item.id]);

  const editing = draft !== null;
  useEffect(() => {
    if (editing) editor.current?.focus();
  }, [editing]);
  const serverChanged = editing && item.description !== draft.baseText;
  const showBanner = serverChanged && bannerDismissedFor !== item.description;

  useEffect(() => {
    if (showBanner) announce("The description changed while you were editing. Your draft is kept.");
  }, [showBanner]);

  const close = () => {
    setDraft(null);
    setConflict(null);
    setReviewing(false);
    setTab("write");
  };

  const submit = async (text: string, version: number) => {
    try {
      const outcome = await save({ description: text }, version);
      if (outcome.kind === "saved") {
        toast.success(outcome.kept ? describeKept(outcome.kept) : "Saved");
        close();
        return;
      }
      setConflict({
        savedNow: outcome.error.current?.description ?? item.description,
        version: outcome.error.current?.version ?? item.version,
        authors: outcome.plan.authors,
      });
    } catch (error) {
      toast.error(describeError(error));
    }
  };

  return (
    <section aria-labelledby="desc-heading" className="mt-8">
      <div className="mb-2 flex items-center justify-between">
        <h2 id="desc-heading" className="text-section">
          Description
        </h2>
        {canEdit && !editing && (
          <Button
            variant="quiet"
            className="h-7 px-2 text-meta"
            onClick={() => {
              setDraft({
                text: item.description,
                baseVersion: item.version,
                baseText: item.description,
              });
            }}
          >
            <Pencil className="size-3.5" strokeWidth={1.75} aria-hidden />
            Edit
          </Button>
        )}
      </div>

      {!editing && (
        <div data-testid="description">
          {item.description.trim() === "" ? (
            <p className="text-body text-pencil">No description yet.</p>
          ) : (
            <Markdown>{item.description}</Markdown>
          )}
        </div>
      )}

      {editing && (
        <div className="flex flex-col gap-3">
          {showBanner && (
            <div
              role="status"
              className="rounded-panel border border-rule bg-p1-wash p-3 text-meta"
            >
              <p>The description changed while you were editing. Your draft is kept.</p>
              <div className="mt-2 flex gap-2">
                <Button
                  variant="secondary"
                  className="h-7 px-2 text-meta"
                  onClick={() => {
                    setReviewing((v) => !v);
                  }}
                >
                  {reviewing ? "Hide changes" : "Review changes"}
                </Button>
                <Button
                  variant="quiet"
                  className="h-7 px-2 text-meta"
                  onClick={() => {
                    setBannerDismissedFor(item.description);
                    setReviewing(false);
                  }}
                >
                  Keep editing
                </Button>
              </div>
              {reviewing && (
                <DiffView className="mt-2" before={draft.baseText} after={item.description} />
              )}
            </div>
          )}

          <div role="tablist" aria-label="Editor" className="flex gap-1">
            {(["write", "preview"] as const).map((name) => (
              <button
                key={name}
                role="tab"
                type="button"
                aria-selected={tab === name}
                onClick={() => {
                  setTab(name);
                }}
                className={cn(
                  "h-7 cursor-pointer rounded-chip px-3 text-meta font-strong",
                  tab === name ? "bg-dispatch-wash text-ink" : "text-pencil hover:bg-dispatch-wash",
                )}
              >
                {name === "write" ? "Write" : "Preview"}
              </button>
            ))}
          </div>
          {tab === "write" ? (
            <Textarea
              ref={editor}
              aria-label="Description"
              value={draft.text}
              maxLength={MAX}
              className="min-h-40"
              onChange={(event) => {
                setDraft({ ...draft, text: event.target.value });
              }}
            />
          ) : (
            <div className="min-h-40 rounded-chip border border-rule p-3">
              {draft.text.trim() === "" ? (
                <p className="text-pencil">Nothing to preview.</p>
              ) : (
                <Markdown>{draft.text}</Markdown>
              )}
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="tnum text-small text-pencil">
              {draft.text.length.toLocaleString()} / {MAX.toLocaleString()}
            </span>
            <div className="flex gap-2">
              <Button variant="quiet" onClick={close} disabled={isPending}>
                Discard draft
              </Button>
              <Button
                variant="primary"
                pending={isPending && conflict === null}
                disabled={draft.text === draft.baseText}
                onClick={() => void submit(draft.text, draft.baseVersion)}
              >
                Save description
              </Button>
            </div>
          </div>
        </div>
      )}

      <ConflictDialog
        open={conflict !== null && draft !== null}
        field="description"
        savedNow={conflict?.savedNow ?? ""}
        mine={draft?.text ?? ""}
        authors={conflict?.authors ?? []}
        saving={isPending}
        onSaveMine={() => {
          if (draft && conflict) void submit(draft.text, conflict.version);
        }}
        onDiscard={close}
        onKeepEditing={() => {
          setConflict(null);
        }}
      />
    </section>
  );
}
