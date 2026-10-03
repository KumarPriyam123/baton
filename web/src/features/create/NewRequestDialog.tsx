/**
 * New request (DESIGN §4.5): team, type, title with similar-request suggestions, description with
 * Preview, priority with a due preview, and the approval/confidentiality line the type implies.
 *
 * Double-submit safety (I6): the form owns ONE idempotency key. It is reused by every submit and
 * every automatic retry of the same content, and replaced only when the user edits the form or
 * the request succeeds. So a double click, or a retry after a lost response, is one request.
 */
import { zodResolver } from "@hookform/resolvers/zod";
import * as ToggleGroup from "@radix-ui/react-toggle-group";
import { useMutation } from "@tanstack/react-query";
import {
  CreditCard,
  Headset,
  ListChecks,
  type LucideIcon,
  ShieldCheck,
  TriangleAlert,
  Wrench,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Controller, useForm } from "react-hook-form";
import Markdown from "react-markdown";
import { useNavigate } from "react-router";
import remarkGfm from "remark-gfm";
import { toast } from "sonner";
import { z } from "zod";

import { useMe, useSimilar, useTeams } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { cn } from "../../components/ui/cn";
import { DialogPanel, DialogRoot } from "../../components/ui/Dialog";
import { Field, Input, Textarea } from "../../components/ui/Field";
import { ApiError, type ItemOut, api, unwrap } from "../../lib/api";
import { TYPES } from "../../lib/filters";
import { DUE_PREVIEW, PRIORITY_LABEL, STATUS_LABEL, TYPE_LABEL } from "../../lib/format";
import { newIdempotencyKey, useCommand } from "../../lib/useCommand";

const schema = z.object({
  team_key: z.string().min(1, "Choose a team."),
  type: z.enum(TYPES),
  title: z
    .string()
    .trim()
    .min(3, "Give the request a title of at least 3 characters.")
    .max(200, "Keep the title under 200 characters."),
  description: z.string().max(20_000, "Keep the description under 20,000 characters."),
  priority: z.number().int().min(0).max(3),
  requires_approval: z.boolean(),
});
type Values = z.infer<typeof schema>;

const TYPE_ICON: Record<(typeof TYPES)[number], LucideIcon> = {
  incident: TriangleAlert,
  customer_issue: Headset,
  payment_investigation: CreditCard,
  engineering: Wrench,
  compliance_request: ShieldCheck,
  ops_task: ListChecks,
};

/** Why approval and confidentiality are set the way they are (SPEC §3.1 type defaults). */
const TYPE_NOTE: Partial<Record<(typeof TYPES)[number], string>> = {
  payment_investigation: "Payment investigations need an approval before they can be resolved.",
  compliance_request:
    "Compliance requests need an approval and are confidential: only you, the team's leads and the assignee can see them.",
};

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebounced(value);
    }, ms);
    return () => {
      window.clearTimeout(timer);
    };
  }, [value, ms]);
  return debounced;
}

export function NewRequestDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <DialogRoot open={open} onOpenChange={onOpenChange}>
      <DialogPanel title="New request" className="w-[min(680px,100%)]">
        <NewRequestForm
          onDone={() => {
            onOpenChange(false);
          }}
        />
      </DialogPanel>
    </DialogRoot>
  );
}

function NewRequestForm({ onDone }: { onDone: () => void }) {
  const navigate = useNavigate();
  const me = useMe();
  const teams = useTeams();
  const [tab, setTab] = useState<"write" | "preview">("write");

  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: {
      team_key: "",
      type: "customer_issue",
      title: "",
      description: "",
      priority: 2,
      requires_approval: false,
    },
  });
  const { register, control, watch, setValue, setError, formState } = form;

  // Default team: one I can raise work in (member or lead), else the first.
  useEffect(() => {
    if (!teams.data || form.getValues("team_key")) return;
    const mine = me.data?.memberships.find((m) => m.role !== "viewer")?.team.key;
    const first = teams.data[0]?.key;
    const chosen = mine ?? first;
    if (chosen) setValue("team_key", chosen);
  }, [teams.data, me.data, form, setValue]);

  // One key per attempt: reused until the user changes what they're asking for.
  const keyRef = useRef(newIdempotencyKey());
  useEffect(() => {
    const subscription = watch(() => {
      keyRef.current = newIdempotencyKey();
    });
    return () => {
      subscription.unsubscribe();
    };
  }, [watch]);

  const create = useCommand<Values, ItemOut>({
    run: (values, ctx) =>
      unwrap(
        api.POST("/api/v1/items", {
          params: { header: { "Idempotency-Key": ctx.idempotencyKey } },
          body: {
            team_key: values.team_key,
            type: values.type,
            title: values.title,
            description: values.description,
            priority: values.priority,
            requires_approval: values.type === "ops_task" ? values.requires_approval : false,
          },
        }),
      ),
    onError: (error) => {
      // Field errors from the server go next to the field, not into a toast.
      if (error instanceof ApiError && error.code === "VALIDATION_FAILED" && error.errors?.length) {
        let mapped = false;
        for (const e of error.errors) {
          if (e.field in schema.shape) {
            setError(e.field as keyof Values, { message: e.message });
            mapped = true;
          }
        }
        return mapped;
      }
      return undefined;
    },
  });

  const submit = form.handleSubmit(async (values) => {
    if (create.isPending) return;
    try {
      const item = await create.execute(values, { idempotencyKey: keyRef.current });
      keyRef.current = newIdempotencyKey();
      toast.success(`Created ${item.key}`, {
        action: {
          label: "Open",
          onClick: () => void navigate(`/items/${item.key}`),
        },
      });
      onDone();
    } catch {
      // useCommand already told the user; the form stays open with their input and the same key.
    }
  });

  const teamKey = watch("team_key");
  const type = watch("type");
  const title = watch("title");
  const description = watch("description");
  const priority = watch("priority");

  const teamId = teams.data?.find((t) => t.key === teamKey)?.id;
  const similar = useSimilar(teamId, useDebounced(title, 300));
  const suggestions = title.trim().length >= 4 ? (similar.data ?? []) : [];

  const watchInstead = useMutation({
    mutationFn: (key: string) =>
      api.PUT("/api/v1/items/{key}/watch", { params: { path: { key } } }),
    onSuccess: (_, key) => {
      toast.success(`Watching ${key}`);
      onDone();
    },
    onError: () => {
      toast.error("Couldn't start watching. Try again.");
    },
  });

  return (
    // Ctrl/Cmd+Enter submits from any field (DESIGN §4.4 uses the same chord for comments); the
    // key events come from the inputs inside, the form itself is not a control.
    // eslint-disable-next-line jsx-a11y/no-noninteractive-element-interactions
    <form
      noValidate
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        void submit(event);
      }}
      onKeyDown={(event) => {
        if ((event.metaKey || event.ctrlKey) && event.key === "Enter") void submit();
      }}
    >
      <Field label="Team" htmlFor="team" error={formState.errors.team_key?.message}>
        <select
          id="team"
          disabled={!teams.data}
          className="h-9 w-full rounded-chip border border-rule bg-sheet px-2 text-body text-ink"
          {...register("team_key")}
        >
          {teams.data?.map((team) => (
            <option key={team.key} value={team.key}>
              {team.name}
            </option>
          ))}
        </select>
      </Field>

      <div className="flex flex-col gap-1">
        <span id="type-label" className="text-meta font-strong">
          Type
        </span>
        <Controller
          control={control}
          name="type"
          render={({ field }) => (
            <ToggleGroup.Root
              type="single"
              value={field.value}
              onValueChange={(value) => {
                if (value) field.onChange(value);
              }}
              aria-labelledby="type-label"
              className="grid grid-cols-2 gap-2 sm:grid-cols-3"
            >
              {TYPES.map((t) => {
                const Icon = TYPE_ICON[t];
                return (
                  <ToggleGroup.Item
                    key={t}
                    value={t}
                    className={cn(
                      "flex h-9 cursor-pointer items-center gap-2 rounded-chip border border-rule px-2 text-meta",
                      "data-[state=on]:border-dispatch data-[state=on]:bg-dispatch-wash data-[state=on]:font-strong",
                    )}
                  >
                    <Icon className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
                    <span className="truncate">{TYPE_LABEL[t]}</span>
                  </ToggleGroup.Item>
                );
              })}
            </ToggleGroup.Root>
          )}
        />
      </div>

      <div className="flex flex-col gap-2">
        <Field label="Title" htmlFor="title" error={formState.errors.title?.message}>
          <Input
            id="title"
            // eslint-disable-next-line jsx-a11y/no-autofocus -- the first thing to fill in
            autoFocus
            autoComplete="off"
            aria-invalid={formState.errors.title ? true : undefined}
            {...register("title")}
          />
        </Field>
        {suggestions.length > 0 && (
          <section aria-label="Similar open requests" className="rounded-panel border border-rule">
            <h3 className="px-3 pt-2 text-meta font-strong">Similar open requests</h3>
            <ul>
              {suggestions.map((s) => (
                <li
                  key={s.id}
                  className="flex items-center gap-3 border-t border-rule px-3 py-2 first:border-t-0"
                >
                  <span className="tnum text-meta text-pencil">{s.key}</span>
                  <span className="min-w-0 flex-1 truncate text-body">{s.title}</span>
                  <span className="shrink-0 text-meta text-pencil">
                    {(STATUS_LABEL as Record<string, string>)[s.status] ?? s.status}
                  </span>
                  <a
                    href={`/items/${s.key}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-meta text-dispatch"
                  >
                    Open
                  </a>
                  <button
                    type="button"
                    className="cursor-pointer text-meta text-dispatch"
                    disabled={watchInstead.isPending}
                    onClick={() => {
                      watchInstead.mutate(s.key);
                    }}
                  >
                    Watch instead
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between">
          <label htmlFor="description" className="text-meta font-strong">
            Description
          </label>
          <div role="tablist" aria-label="Description view" className="flex gap-1">
            {(["write", "preview"] as const).map((name) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={tab === name}
                onClick={() => {
                  setTab(name);
                }}
                className={cn(
                  "h-7 cursor-pointer rounded-chip px-2 text-meta",
                  tab === name ? "bg-dispatch-wash font-strong" : "text-pencil",
                )}
              >
                {name === "write" ? "Write" : "Preview"}
              </button>
            ))}
          </div>
        </div>
        {tab === "write" ? (
          <Textarea
            id="description"
            aria-invalid={formState.errors.description ? true : undefined}
            placeholder="What's happening, and what would done look like? Markdown works."
            {...register("description")}
          />
        ) : (
          <div className="markdown min-h-24 rounded-chip border border-rule p-3 text-body">
            {description.trim() ? (
              // No rehype-raw: raw HTML in the text stays text (I12).
              <Markdown
                remarkPlugins={[remarkGfm]}
                components={{
                  a: ({ children, href }) => (
                    <a href={href} target="_blank" rel="noopener noreferrer">
                      {children}
                    </a>
                  ),
                }}
              >
                {description}
              </Markdown>
            ) : (
              <p className="text-pencil">Nothing to preview yet.</p>
            )}
          </div>
        )}
        {formState.errors.description && (
          <p role="alert" className="text-meta text-p0">
            {formState.errors.description.message}
          </p>
        )}
      </div>

      <div className="flex flex-col gap-1">
        <span id="priority-label" className="text-meta font-strong">
          Priority
        </span>
        <div className="flex items-center gap-3">
          <Controller
            control={control}
            name="priority"
            render={({ field }) => (
              <ToggleGroup.Root
                type="single"
                value={String(field.value)}
                onValueChange={(value) => {
                  if (value) field.onChange(Number(value));
                }}
                aria-labelledby="priority-label"
                className="flex gap-1"
              >
                {PRIORITY_LABEL.map((label, p) => (
                  <ToggleGroup.Item
                    key={label}
                    value={String(p)}
                    className="h-9 min-w-12 cursor-pointer rounded-chip border border-rule px-3 text-meta data-[state=on]:border-dispatch data-[state=on]:bg-dispatch-wash data-[state=on]:font-strong"
                  >
                    {label}
                  </ToggleGroup.Item>
                ))}
              </ToggleGroup.Root>
            )}
          />
          <span className="text-meta text-pencil">{DUE_PREVIEW[priority]}</span>
        </div>
      </div>

      {type === "ops_task" ? (
        <label className="flex items-center gap-2 text-body">
          <input
            type="checkbox"
            className="size-4 accent-dispatch"
            {...register("requires_approval")}
          />
          Require an approval before this can be resolved
        </label>
      ) : TYPE_NOTE[type] ? (
        <p className="text-meta text-pencil">{TYPE_NOTE[type]}</p>
      ) : null}

      <div className="mt-2 flex items-center justify-end gap-2">
        <Button variant="quiet" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" pending={create.isPending}>
          Create request
        </Button>
      </div>
    </form>
  );
}
