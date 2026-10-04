/**
 * Command palette (DESIGN §4.12), Ctrl/Cmd + K. Jump to an item by key, search items, go to a
 * screen, and run the open item's actions. Actions come from `allowed_actions` through the same
 * `useItemActions` / `useCommand` path as the action bar (I4, I6, I13); nothing here decides what
 * is allowed. Radix Dialog traps focus and closes on Esc; cmdk does the arrow-key list.
 */
import * as RadixDialog from "@radix-ui/react-dialog";
import { useQuery } from "@tanstack/react-query";
import { Command } from "cmdk";
import {
  CornerDownLeft,
  Eye,
  EyeOff,
  Gavel,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Loader2,
  Plus,
  Search,
  Send,
  Settings,
  Timer,
  Users,
} from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router";

import { useItem, useMe, useMembers, useTeams } from "../../api/queries";
import { queueLink } from "../../app/shell/Rail";
import { useShell } from "../../app/shell/Shell";
import { Button } from "../../components/ui/Button";
import { Input } from "../../components/ui/Field";
import { Kbd } from "../../components/ui/FilterToken";
import { StatusIcon } from "../../components/ui/Strip";
import { type ItemOut, api, unwrap } from "../../lib/api";
import { OPEN_STATUSES } from "../../lib/filters";
import { PRIORITY_LABEL } from "../../lib/format";
import { simpleInput } from "../item/ActionBar";
import type { ActionSpec } from "../item/actions";
import { useItemActions } from "../item/useItemActions";
import type { ActionInput } from "../item/useItemActions";
import { usePriorityEdit, useWatch } from "../item/Properties";
import { KEY_PATTERN, canWatch, openItemKey, paletteActionSpecs, priorityChoices } from "./model";

const SEARCH_DEBOUNCE_MS = 200;
const SEARCH_LIMIT = 8;

/** What the second step of the palette is collecting. */
type Page = { kind: "reason"; spec: ActionSpec } | { kind: "assign" };

interface ItemContext {
  item: ItemOut;
  specs: ActionSpec[];
  priorities: { value: number; label: string }[];
  watchable: boolean;
  pending: boolean;
  runSpec: (spec: ActionSpec) => void;
  assign: (person: { id: string; name: string }) => Promise<boolean>;
  submitReason: (spec: ActionSpec, reason: string) => Promise<boolean>;
  setPriority: (value: number, reason?: string) => Promise<boolean>;
  /** Set when the server asked why a priority is being lowered. */
  priorityReasonFor: number | null;
  cancelPriorityReason: () => void;
  toggleWatch: () => Promise<void>;
  members: { id: string; name: string }[];
}

export function CommandPalette() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
      if (event.key.toLowerCase() !== "k") return;
      event.preventDefault();
      setOpen((current) => !current);
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
    };
  }, []);

  return (
    <RadixDialog.Root open={open} onOpenChange={setOpen}>
      <RadixDialog.Portal>
        <RadixDialog.Overlay className="fade-in fixed inset-0 z-40 bg-ink/40" />
        <PaletteSurface
          close={() => {
            setOpen(false);
          }}
        />
      </RadixDialog.Portal>
    </RadixDialog.Root>
  );
}

/** Mounted only while open, so every open starts with an empty query and a fresh item lookup. */
function PaletteSurface({ close }: { close: () => void }) {
  const location = useLocation();
  const key = openItemKey(location.pathname);
  const item = useItem(key);
  // Esc steps back out of a second page before it closes the palette: the page registers here.
  const backRef = useRef<(() => boolean) | null>(null);

  return (
    <RadixDialog.Content
      aria-describedby={undefined}
      onEscapeKeyDown={(event) => {
        if (backRef.current?.()) event.preventDefault();
      }}
      className="float-in fixed top-[12vh] left-1/2 z-50 flex max-h-[min(560px,76dvh)] w-[min(640px,calc(100%-32px))] -translate-x-1/2 flex-col overflow-hidden rounded-panel border border-rule bg-sheet shadow-float"
    >
      <RadixDialog.Title className="sr-only-live">Command palette</RadixDialog.Title>
      {item.data ? (
        <WithItem item={item.data} close={close} backRef={backRef} />
      ) : (
        <Body close={close} backRef={backRef} />
      )}
    </RadixDialog.Content>
  );
}

function WithItem({
  item,
  close,
  backRef,
}: {
  item: ItemOut;
  close: () => void;
  backRef: React.RefObject<(() => boolean) | null>;
}) {
  const actions = useItemActions(item);
  const priority = usePriorityEdit(item);
  const watch = useWatch(item);
  const members = useMembers(item.team.key);

  const context: ItemContext = {
    item,
    specs: paletteActionSpecs(item),
    priorities: priorityChoices(item),
    watchable: canWatch(item),
    pending: actions.pending !== null,
    runSpec: (spec) => {
      const input = simpleInput(spec, item);
      if (input)
        void actions.run(input).then((ok) => {
          if (ok) close();
        });
    },
    assign: async (person) => {
      const ok = await actions.run({ action: "assign", assigneeId: person.id, name: person.name });
      if (ok) close();
      return ok;
    },
    submitReason: async (spec, reason) => {
      const input = reasonInput(spec, item, reason);
      if (!input) return false;
      const ok = await actions.run(input);
      if (ok) close();
      return ok;
    },
    setPriority: async (value, reason) => {
      const ok = await priority.change(value, reason);
      if (ok) close();
      return ok;
    },
    priorityReasonFor: priority.reasonFor,
    cancelPriorityReason: priority.cancelReason,
    toggleWatch: async () => {
      await watch.toggle();
      close();
    },
    members: (members.data?.items ?? []).map((m) => ({ id: m.user.id, name: m.user.name })),
  };
  return <Body close={close} backRef={backRef} context={context} />;
}

/** The input for an action that collects one reason. Absent: not an action the palette sends. */
function reasonInput(spec: ActionSpec, item: ItemOut, reason: string): ActionInput | null {
  const text = reason.trim() === "" ? undefined : reason.trim();
  switch (spec.action) {
    case "unassign":
      return text ? { action: "unassign", reason: text } : { action: "unassign" };
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
}

const groupClass =
  "px-1 pb-1 [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 " +
  "[&_[cmdk-group-heading]]:text-meta [&_[cmdk-group-heading]]:font-strong " +
  "[&_[cmdk-group-heading]]:text-pencil";

function Row({
  value,
  onSelect,
  children,
  disabled,
}: {
  value: string;
  onSelect: () => void;
  children: ReactNode;
  disabled?: boolean;
}) {
  return (
    <Command.Item
      value={value}
      onSelect={onSelect}
      {...(disabled ? { disabled: true } : {})}
      className="flex h-9 cursor-pointer items-center gap-3 rounded-chip px-2 text-body text-ink data-[disabled=true]:cursor-not-allowed data-[disabled=true]:opacity-50 data-[selected=true]:bg-dispatch-wash"
    >
      {children}
    </Command.Item>
  );
}

function Icon({ children }: { children: ReactNode }) {
  return <span className="flex size-4 shrink-0 items-center text-pencil">{children}</span>;
}

const ICON = { className: "size-4", strokeWidth: 1.75, "aria-hidden": true } as const;

function Body({
  close,
  backRef,
  context,
}: {
  close: () => void;
  backRef: React.RefObject<(() => boolean) | null>;
  context?: ItemContext;
}) {
  const navigate = useNavigate();
  const shell = useShell();
  const me = useMe();
  const teams = useTeams();
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [page, setPage] = useState<Page | null>(null);
  const timer = useRef<number | undefined>(undefined);

  useEffect(
    () => () => {
      window.clearTimeout(timer.current);
    },
    [],
  );

  const onQuery = (value: string) => {
    setQuery(value);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      setDebounced(value.trim());
    }, SEARCH_DEBOUNCE_MS);
  };

  const enter = (next: Page) => {
    window.clearTimeout(timer.current);
    setQuery("");
    setDebounced("");
    setPage(next);
  };

  const priorityReason = context?.priorityReasonFor ?? null;
  const showingPage = page !== null || priorityReason !== null;
  useEffect(() => {
    backRef.current = () => {
      if (priorityReason !== null) {
        context?.cancelPriorityReason();
        return true;
      }
      if (page) {
        setPage(null);
        return true;
      }
      return false;
    };
    return () => {
      backRef.current = null;
    };
  });

  const search = useQuery({
    queryKey: ["palette", "search", debounced],
    queryFn: () =>
      unwrap(
        api.GET("/api/v1/items", { params: { query: { q: debounced, limit: SEARCH_LIMIT } } }),
      ),
    enabled: debounced.length >= 2 && !showingPage,
    staleTime: 15_000,
  });

  const go = (to: string) => {
    close();
    void navigate(to);
  };

  if (context && priorityReason !== null) {
    return (
      <ReasonPage
        title={`Change priority to ${PRIORITY_LABEL[priorityReason] ?? ""}`}
        label="Why is it changing?"
        submit="Change priority"
        required
        onBack={context.cancelPriorityReason}
        onSubmit={(reason) => context.setPriority(priorityReason, reason)}
      />
    );
  }
  if (context && page?.kind === "reason" && page.spec.form) {
    const spec = page.spec;
    return (
      <ReasonPage
        title={page.spec.form.title}
        label={page.spec.form.reasonLabel ?? "Reason"}
        submit={page.spec.form.submit}
        required={page.spec.form.reasonRequired ?? false}
        danger={page.spec.danger ?? false}
        {...(page.spec.form.placeholder ? { placeholder: page.spec.form.placeholder } : {})}
        onBack={() => {
          setPage(null);
        }}
        onSubmit={(reason) => context.submitReason(spec, reason)}
      />
    );
  }
  if (context && page?.kind === "assign") {
    return (
      <Command label="Assign to" shouldFilter className="flex min-h-0 flex-col">
        <PaletteInput
          value={query}
          onChange={setQuery}
          placeholder={`Assign ${context.item.key} to…`}
        />
        <Command.List className="min-h-0 flex-1 overflow-y-auto p-1">
          <Command.Empty className="px-3 py-6 text-body text-pencil">
            No one on {context.item.team.name} matches.
          </Command.Empty>
          {context.members.map((person) => (
            <Row
              key={person.id}
              value={person.name}
              onSelect={() => void context.assign(person)}
              disabled={context.pending}
            >
              <Icon>
                <Users {...ICON} />
              </Icon>
              {person.name}
            </Row>
          ))}
        </Command.List>
        <Footer hint="Esc goes back" />
      </Command>
    );
  }

  const text = query.trim().toLowerCase();
  const matches = (label: string) => text === "" || label.toLowerCase().includes(text);
  const typedKey = KEY_PATTERN.test(query.trim()) ? query.trim().toUpperCase() : null;

  const open = [...OPEN_STATUSES];
  const isAdmin = me.data?.user.is_admin ?? false;
  const teamEntries = (teams.data ?? [])
    .filter((t) => t.my_role !== null || isAdmin)
    .flatMap((t) => [
      {
        label: `${t.name} queue`,
        to: queueLink({ team: t.key, status: open }),
        icon: <Users {...ICON} />,
      },
      ...(t.my_role === "lead" || isAdmin
        ? [
            {
              label: `${t.name} settings`,
              to: `/teams/${t.key}/settings`,
              icon: <Settings {...ICON} />,
            },
          ]
        : []),
    ]);
  const screens = [
    { label: "Inbox", to: "/inbox", icon: <Inbox {...ICON} /> },
    { label: "Queue", to: "/items", icon: <ListChecks {...ICON} /> },
    {
      label: "My work",
      to: queueLink({ assignee: "me", status: open }),
      icon: <ListChecks {...ICON} />,
    },
    { label: "My requests", to: queueLink({ requester: "me" }), icon: <Send {...ICON} /> },
    { label: "Dashboard", to: "/dashboard", icon: <LayoutDashboard {...ICON} /> },
    { label: "Decisions", to: "/decisions", icon: <Gavel {...ICON} /> },
    ...(isAdmin ? [{ label: "Jobs", to: "/admin/jobs", icon: <Timer {...ICON} /> }] : []),
    ...teamEntries,
  ].filter((entry) => matches(entry.label));

  const specs = (context?.specs ?? []).filter((spec) => matches(spec.label));
  const priorities = (context?.priorities ?? []).filter((choice) => matches(choice.label));
  const watchLabel = context?.item.watching ? "Stop watching" : "Watch";
  const showWatch = context?.watchable === true && matches(watchLabel);
  const found = search.data?.items ?? [];
  const showNew = matches("New request");

  return (
    <Command label="Command palette" shouldFilter={false} loop className="flex min-h-0 flex-col">
      <PaletteInput
        value={query}
        onChange={onQuery}
        placeholder={
          context ? `Search, or act on ${context.item.key}` : "Search requests or jump to…"
        }
      />
      <Command.List className="min-h-0 flex-1 overflow-y-auto p-1">
        <Command.Empty className="px-3 py-6 text-body text-pencil">
          {search.isFetching ? "Searching…" : `Nothing matches "${query.trim()}".`}
        </Command.Empty>

        {typedKey && (
          <Command.Group heading="Jump to" className={groupClass}>
            <Row
              value={`go ${typedKey}`}
              onSelect={() => {
                go(`/items/${typedKey}`);
              }}
            >
              <Icon>
                <CornerDownLeft {...ICON} />
              </Icon>
              <span>
                Open <span className="tnum font-strong">{typedKey}</span>
              </span>
            </Row>
          </Command.Group>
        )}

        {context && (specs.length > 0 || priorities.length > 0 || showWatch) && (
          <Command.Group heading={`Actions on ${context.item.key}`} className={groupClass}>
            {specs.map((spec) => (
              <Row
                key={spec.action}
                value={`action ${spec.action}`}
                disabled={context.pending}
                onSelect={() => {
                  if (spec.form?.kind === "assign") enter({ kind: "assign" });
                  else if (spec.form) enter({ kind: "reason", spec });
                  else context.runSpec(spec);
                }}
              >
                {context.pending ? (
                  <Icon>
                    <Loader2 {...ICON} />
                  </Icon>
                ) : null}
                {spec.label}
              </Row>
            ))}
            {priorities.map((choice) => (
              <Row
                key={choice.value}
                value={`priority ${String(choice.value)}`}
                onSelect={() => void context.setPriority(choice.value)}
              >
                {choice.label}
              </Row>
            ))}
            {showWatch && (
              <Row value="action watch" onSelect={() => void context.toggleWatch()}>
                <Icon>{context.item.watching ? <EyeOff {...ICON} /> : <Eye {...ICON} />}</Icon>
                {watchLabel}
              </Row>
            )}
          </Command.Group>
        )}

        {found.length > 0 && (
          <Command.Group heading="Requests" className={groupClass}>
            {found.map((hit) => (
              <Row
                key={hit.key}
                value={`item ${hit.key}`}
                onSelect={() => {
                  go(`/items/${hit.key}`);
                }}
              >
                <span className="tnum w-20 shrink-0 text-meta text-pencil">{hit.key}</span>
                <StatusIcon status={hit.status} />
                <span className="min-w-0 flex-1 truncate">{hit.title}</span>
              </Row>
            ))}
          </Command.Group>
        )}

        {(showNew || screens.length > 0) && (
          <Command.Group heading="Go to" className={groupClass}>
            {showNew && (
              <Row
                value="create new request"
                onSelect={() => {
                  close();
                  shell.openNewRequest();
                }}
              >
                <Icon>
                  <Plus {...ICON} />
                </Icon>
                New request
              </Row>
            )}
            {screens.map((entry) => (
              <Row
                key={entry.to}
                value={`go ${entry.to}`}
                onSelect={() => {
                  go(entry.to);
                }}
              >
                <Icon>{entry.icon}</Icon>
                {entry.label}
              </Row>
            ))}
          </Command.Group>
        )}
      </Command.List>
      <Footer hint={text === "" ? "Type to search" : "Enter to choose"} />
    </Command>
  );
}

function PaletteInput({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (value: string) => void;
  placeholder: string;
}) {
  return (
    <div className="flex h-12 shrink-0 items-center gap-3 border-b border-rule px-4">
      <Search className="size-4 shrink-0 text-pencil" strokeWidth={1.75} aria-hidden />
      <Command.Input
        value={value}
        onValueChange={onChange}
        placeholder={placeholder}
        // eslint-disable-next-line jsx-a11y/no-autofocus -- a second page must put focus back in the field
        autoFocus
        className="h-full min-w-0 flex-1 bg-transparent text-body text-ink outline-none placeholder:text-pencil"
      />
    </div>
  );
}

function Footer({ hint }: { hint: string }) {
  return (
    <div className="flex h-9 shrink-0 items-center justify-between border-t border-rule px-4 text-small text-pencil">
      <span>{hint}</span>
      <span className="flex items-center gap-1">
        <Kbd>↑</Kbd>
        <Kbd>↓</Kbd>
        <Kbd>Esc</Kbd>
      </span>
    </div>
  );
}

function ReasonPage({
  title,
  label,
  submit,
  required,
  danger = false,
  placeholder,
  onBack,
  onSubmit,
}: {
  title: string;
  label: string;
  submit: string;
  required: boolean;
  danger?: boolean;
  placeholder?: string;
  onBack: () => void;
  onSubmit: (reason: string) => Promise<boolean>;
}) {
  const [reason, setReason] = useState("");
  const [pending, setPending] = useState(false);
  const missing = required && reason.trim() === "";

  return (
    <form
      className="flex flex-col gap-3 p-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (missing || pending) return;
        setPending(true);
        void onSubmit(reason).finally(() => {
          setPending(false);
        });
      }}
    >
      <h3 className="text-section">{title}</h3>
      <label className="flex flex-col gap-1 text-meta font-strong" htmlFor="palette-reason">
        {label}
        <Input
          id="palette-reason"
          // eslint-disable-next-line jsx-a11y/no-autofocus -- a second page must put focus back in the field
          autoFocus
          value={reason}
          placeholder={placeholder}
          onChange={(event) => {
            setReason(event.target.value);
          }}
        />
      </label>
      <div className="flex justify-end gap-2">
        <Button variant="quiet" onClick={onBack}>
          Back
        </Button>
        <Button
          type="submit"
          variant={danger ? "danger" : "primary"}
          pending={pending}
          disabled={missing}
        >
          {submit}
        </Button>
      </div>
    </form>
  );
}
