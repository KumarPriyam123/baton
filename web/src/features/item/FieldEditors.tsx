/**
 * Inline editors for the item's own fields (DESIGN §4.4, SPEC §4.2): title, due date, type,
 * confidential, requires approval. Each is drawn only when the server put its action in
 * `allowed_actions` (I4), and each edit is one PATCH through `useFieldEdit` (useCommand, If-Match,
 * the 412 rebase). Priority and the description have their own editors.
 */
import { ChevronDown, Pencil } from "lucide-react";
import { type ReactNode, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Input, Textarea } from "../../components/ui/Field";
import {
  MenuContent,
  MenuRadio,
  MenuRadioGroup,
  MenuRoot,
  MenuTrigger,
} from "../../components/ui/Menu";
import { PopoverAnchor, PopoverContent, PopoverRoot } from "../../components/ui/Popover";
import type { ItemOut } from "../../lib/api";
import { ConflictDialog } from "./ConflictDialog";
import { useFieldEdit } from "./useFieldEdit";
import type { PatchFields } from "./useSaveFields";

const editButton =
  "inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-chip text-pencil hover:bg-dispatch-wash";

/** The title, editable in place. Enter saves, Esc cancels; a same-field 412 opens the diff dialog. */
export function TitleEditor({ item, washed }: { item: ItemOut; washed: boolean }) {
  const canEdit = item.allowed_actions.includes("edit_text");
  const { edit, isPending } = useFieldEdit(item);
  const [draft, setDraft] = useState<{ text: string; version: number } | null>(null);
  const [conflict, setConflict] = useState<{ savedNow: string; authors: string[] } | null>(null);

  const save = async (text: string) => {
    const result = await edit({ title: text }, "Title saved");
    if (result.status === "saved") {
      setDraft(null);
      setConflict(null);
    } else if (result.status === "conflict") {
      setConflict({ savedNow: result.current?.title ?? item.title, authors: result.authors });
    }
  };

  if (!draft) {
    return (
      <div className="mt-1 flex items-start gap-1">
        <h2 className={washed ? "wash text-item" : "text-item"}>{item.title}</h2>
        {canEdit && (
          <button
            type="button"
            aria-label="Edit title"
            className={editButton}
            onClick={() => {
              setDraft({ text: item.title, version: item.version });
            }}
          >
            <Pencil className="size-4" strokeWidth={1.75} aria-hidden />
          </button>
        )}
      </div>
    );
  }

  const unchanged = draft.text.trim() === item.title;
  return (
    <>
      <form
        className="mt-1 flex flex-col gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (!unchanged && draft.text.trim() !== "") void save(draft.text.trim());
        }}
      >
        <label className="sr-only-live" htmlFor="title-edit">
          Title
        </label>
        <Input
          id="title-edit"
          // eslint-disable-next-line jsx-a11y/no-autofocus -- the person just chose to edit it
          autoFocus
          value={draft.text}
          className="text-item h-11"
          onChange={(event) => {
            setDraft({ ...draft, text: event.target.value });
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              setDraft(null);
            }
          }}
        />
        <div className="flex gap-2">
          <Button
            type="submit"
            variant="primary"
            pending={isPending}
            disabled={unchanged || draft.text.trim() === ""}
          >
            Save title
          </Button>
          <Button
            variant="quiet"
            onClick={() => {
              setDraft(null);
            }}
          >
            Cancel
          </Button>
        </div>
      </form>
      <ConflictDialog
        open={conflict !== null}
        field="title"
        savedNow={conflict?.savedNow ?? ""}
        mine={draft.text}
        authors={conflict?.authors ?? []}
        saving={isPending}
        onSaveMine={() => {
          // Send on the version the user now sees: they have read what replaces it.
          void save(draft.text.trim());
        }}
        onDiscard={() => {
          setConflict(null);
          setDraft(null);
        }}
        onKeepEditing={() => {
          setConflict(null);
        }}
      />
    </>
  );
}

/** `datetime-local` wants "2026-10-04T14:30" in local time. */
export function toLocalInput(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${String(d.getFullYear())}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/** The due date: a pencil next to it opens one date-time field. Setting it makes it manual. */
export function DueEditor({ item, children }: { item: ItemOut; children: ReactNode }) {
  const canEdit = item.allowed_actions.includes("edit_due_at");
  const { edit, announceConflict, isPending } = useFieldEdit(item);
  const [value, setValue] = useState<string | null>(null);

  if (!canEdit) return <>{children}</>;
  return (
    <PopoverRoot
      open={value !== null}
      onOpenChange={(open) => {
        if (!open) setValue(null);
      }}
    >
      <PopoverAnchor asChild>
        <span className="flex items-center gap-1">
          {children}
          <button
            type="button"
            aria-label="Change due date"
            className={editButton}
            onClick={() => {
              setValue(toLocalInput(item.due_at ?? new Date().toISOString()));
            }}
          >
            <Pencil className="size-4" strokeWidth={1.75} aria-hidden />
          </button>
        </span>
      </PopoverAnchor>
      {value !== null && (
        <PopoverContent label="Change due date">
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              const when = new Date(value);
              if (Number.isNaN(when.getTime())) return;
              void edit({ due_at: when.toISOString() }, "Due date saved").then((result) => {
                if (result.status === "saved") setValue(null);
                else if (result.status === "conflict") {
                  announceConflict(result, "due_at");
                  setValue(null);
                }
              });
            }}
          >
            <h3 className="text-section">Change due date</h3>
            <label className="flex flex-col gap-1 text-meta font-strong" htmlFor="due-edit">
              Due
              <Input
                id="due-edit"
                type="datetime-local"
                value={value}
                onChange={(event) => {
                  setValue(event.target.value);
                }}
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button
                variant="quiet"
                onClick={() => {
                  setValue(null);
                }}
              >
                Cancel
              </Button>
              <Button type="submit" variant="primary" pending={isPending} disabled={value === ""}>
                Save due date
              </Button>
            </div>
          </form>
        </PopoverContent>
      )}
    </PopoverRoot>
  );
}

/**
 * A property with a short list of values (type, confidential, requires approval): a menu, one
 * PATCH per pick. `reason` makes the pick ask why first (turning approval off, SPEC §4.2); the
 * server can also ask (REASON_REQUIRED) and the same step appears.
 */
export function PropertyChoice({
  item,
  field,
  allowed,
  current,
  options,
  fields,
  reason,
  children,
  ariaLabel,
}: {
  item: ItemOut;
  field: string;
  /** Which values the server lets this person pick; none: plain text. */
  allowed: (value: string) => boolean;
  current: string;
  options: { value: string; label: string }[];
  fields: (value: string) => PatchFields;
  /** Pick of this value always asks for a reason first. */
  reason?: (value: string) => { title: string; label: string } | undefined;
  children: ReactNode;
  ariaLabel: string;
}) {
  const { edit, announceConflict, isPending } = useFieldEdit(item);
  const [asking, setAsking] = useState<{ value: string; title: string; label: string } | null>(
    null,
  );
  const [why, setWhy] = useState("");
  const pickable = options.filter((o) => o.value === current || allowed(o.value));

  if (pickable.length <= 1) return <>{children}</>;

  const send = async (value: string, text?: string) => {
    const result = await edit({ ...fields(value), ...(text ? { reason: text } : {}) });
    if (result.status === "saved") {
      setAsking(null);
      setWhy("");
    } else if (result.status === "conflict") {
      announceConflict(result, field);
      setAsking(null);
    } else if (result.status === "reason") {
      setAsking({ value, title: "Add a reason", label: "Why is it changing?" });
    }
  };

  return (
    <PopoverRoot
      open={asking !== null}
      onOpenChange={(open) => {
        if (!open) {
          setAsking(null);
          setWhy("");
        }
      }}
    >
      <PopoverAnchor asChild>
        <span className="inline-flex">
          <MenuRoot>
            <MenuTrigger asChild>
              <button
                type="button"
                aria-label={ariaLabel}
                disabled={isPending}
                className="-ml-1 inline-flex cursor-pointer items-center gap-1 rounded-chip px-1 hover:bg-dispatch-wash"
              >
                {children}
                <ChevronDown className="size-4 text-pencil" strokeWidth={1.75} aria-hidden />
              </button>
            </MenuTrigger>
            <MenuContent>
              <MenuRadioGroup
                value={current}
                onValueChange={(value) => {
                  const ask = reason?.(value);
                  if (ask) setAsking({ value, ...ask });
                  else void send(value);
                }}
              >
                {pickable.map((option) => (
                  <MenuRadio key={option.value} value={option.value}>
                    {option.label}
                  </MenuRadio>
                ))}
              </MenuRadioGroup>
            </MenuContent>
          </MenuRoot>
        </span>
      </PopoverAnchor>
      {asking && (
        <PopoverContent label={asking.title}>
          <form
            className="flex flex-col gap-3"
            onSubmit={(event) => {
              event.preventDefault();
              if (why.trim() !== "") void send(asking.value, why.trim());
            }}
          >
            <h3 className="text-section">{asking.title}</h3>
            <label className="flex flex-col gap-1 text-meta font-strong" htmlFor="field-reason">
              {asking.label}
              <Textarea
                id="field-reason"
                value={why}
                className="min-h-20 font-body"
                onChange={(event) => {
                  setWhy(event.target.value);
                }}
              />
            </label>
            <div className="flex justify-end gap-2">
              <Button
                variant="quiet"
                onClick={() => {
                  setAsking(null);
                  setWhy("");
                }}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                pending={isPending}
                disabled={why.trim() === ""}
              >
                Save
              </Button>
            </div>
          </form>
        </PopoverContent>
      )}
    </PopoverRoot>
  );
}
