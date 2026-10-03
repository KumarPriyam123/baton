/**
 * "This request changed while you were editing" (DESIGN §6.2): what is saved now against what
 * you wrote, as a word-level diff, and two honest choices. Closing it (Esc) decides nothing and
 * keeps the draft.
 */
import { useEffect } from "react";

import { Button } from "../../components/ui/Button";
import { DialogPanel, DialogRoot } from "../../components/ui/Dialog";
import { DiffView } from "../../components/ui/DiffView";
import { announce } from "../../lib/announce";

export function ConflictDialog({
  open,
  field,
  savedNow,
  mine,
  authors,
  saving,
  onSaveMine,
  onDiscard,
  onKeepEditing,
}: {
  open: boolean;
  /** "description" */
  field: string;
  savedNow: string;
  mine: string;
  authors: string[];
  saving: boolean;
  onSaveMine: () => void;
  onDiscard: () => void;
  onKeepEditing: () => void;
}) {
  const who = authors.length > 0 ? authors.join(" and ") : "Someone";
  useEffect(() => {
    if (open)
      announce(`This request changed while you were editing. ${who} changed the ${field}.`, true);
  }, [open, who, field]);

  return (
    <DialogRoot
      open={open}
      onOpenChange={(next) => {
        if (!next) onKeepEditing();
      }}
    >
      <DialogPanel
        title="This request changed while you were editing"
        description={`${who} changed the ${field}. Below: what is saved now, with your draft's changes marked.`}
      >
        <div className="flex flex-col gap-3">
          <div>
            <h3 className="mb-1 text-section">Saved now → your draft</h3>
            <DiffView before={savedNow} after={mine} />
          </div>
          <p className="text-meta text-pencil">
            Saving yours replaces {authors.length > 0 ? `${authors.join(" and ")}'s` : "their"}{" "}
            {field}.
          </p>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={onDiscard} disabled={saving}>
              Discard my draft
            </Button>
            <Button variant="primary" onClick={onSaveMine} pending={saving}>
              Save my version
            </Button>
          </div>
        </div>
      </DialogPanel>
    </DialogRoot>
  );
}
