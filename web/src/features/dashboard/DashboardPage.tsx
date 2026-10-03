/** Dashboard (DESIGN §4.6): a table, because the job is comparing teams. Every figure is a link
 * to the queue with the matching filter, except "going quiet", which the queue cannot filter yet
 * (KNOWN_LIMITATIONS): a number that opens a different list would be worse than plain text. */
import { ChevronDown, ChevronRight } from "lucide-react";
import { Fragment, useState } from "react";
import { Link } from "react-router";

import type { components } from "../../api/generated";
import { useTeamStats } from "../../api/queries";
import { Avatar } from "../../components/ui/Avatar";
import { cn } from "../../components/ui/cn";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { StatusIcon } from "../../components/ui/Strip";
import { type ListFilters, OPEN_STATUSES, emptyFilters, serializeFilters } from "../../lib/filters";
import { PRIORITY_LABEL, ageShort, fullTime, plural } from "../../lib/format";
import { useNow } from "../../lib/useNow";

type TeamStats = components["schemas"]["TeamStatsOut"];

const OPEN = [...OPEN_STATUSES];
const BAR = ["bg-p0", "bg-p1", "bg-p2", "bg-p3"] as const;
const PRIORITY_KEYS = ["p0", "p1", "p2", "p3"] as const;

function queueUrl(filters: Partial<ListFilters>): string {
  return `/items?${serializeFilters({ ...emptyFilters(), ...filters }).toString()}`;
}

/** A figure that opens the matching queue; zero is shown, quietly, and still links. */
function Figure({ value, to, label }: { value: number; to: string; label: string }) {
  return (
    <Link
      to={to}
      aria-label={`${String(value)} ${label}`}
      className={cn(
        "tnum text-body no-underline hover:underline",
        value === 0 ? "text-pencil" : "text-ink",
      )}
    >
      {value}
    </Link>
  );
}

function Headline({
  value,
  label,
  to,
  tone,
}: {
  value: number;
  label: string;
  to?: string;
  tone?: "alert";
}) {
  const number = (
    <span
      className={cn(
        "tnum text-figure font-heading",
        tone === "alert" && value > 0 ? "text-p0" : "text-ink",
      )}
    >
      {value}
    </span>
  );
  return (
    <div className="flex flex-col">
      {to ? (
        <Link
          to={to}
          className="no-underline hover:underline"
          aria-label={`${String(value)} ${label}`}
        >
          {number}
        </Link>
      ) : (
        number
      )}
      <span className="text-meta text-pencil">{label}</span>
    </div>
  );
}

function PriorityBar({ team, scale }: { team: TeamStats; scale: number }) {
  const width = scale > 0 ? (team.open / scale) * 100 : 0;
  return (
    <div className="flex items-center gap-3">
      <div className="h-2 flex-1 rounded-chip bg-skeleton" aria-hidden>
        <div
          className="flex h-full overflow-hidden rounded-chip"
          style={{ width: `${String(width)}%` }}
        >
          {PRIORITY_KEYS.map((k, p) => (
            <div
              key={k}
              className={BAR[p]}
              style={{
                width: team.open > 0 ? `${String((team.by_priority[k] / team.open) * 100)}%` : 0,
              }}
            />
          ))}
        </div>
      </div>
      <span className="flex gap-2 text-small text-pencil">
        {PRIORITY_KEYS.map((k, p) => (
          <Link
            key={k}
            to={queueUrl({ team: team.team.key, status: OPEN, priority: [p] })}
            aria-label={`${String(team.by_priority[k])} ${PRIORITY_LABEL[p] ?? ""} open`}
            className={cn(
              "tnum no-underline hover:underline",
              team.by_priority[k] === 0 ? "text-pencil" : "text-ink",
            )}
          >
            <span className="text-pencil">{PRIORITY_LABEL[p]}</span> {team.by_priority[k]}
          </Link>
        ))}
      </span>
    </div>
  );
}

const COLS =
  "grid grid-cols-[minmax(150px,1fr)_minmax(260px,2fr)_repeat(7,72px)] items-center gap-x-3";
const HEADERS = [
  "New",
  "In progress",
  "Blocked",
  "Awaiting",
  "Unowned",
  "Overdue",
  "Quiet",
] as const;

function TeamRow({
  team,
  scale,
  now,
  expanded,
  onToggle,
}: {
  team: TeamStats;
  scale: number;
  now: number;
  expanded: boolean;
  onToggle: () => void;
}) {
  const key = team.team.key;
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const status = (s: keyof TeamStats["by_status"], label: string) => (
    <Figure
      value={team.by_status[s]}
      to={queueUrl({ team: key, status: [s] })}
      label={`${label} in ${team.team.name}`}
    />
  );
  return (
    <Fragment>
      <div role="row" className={cn(COLS, "min-h-12 border-b border-rule px-4 py-2")}>
        <div role="cell" className="flex min-w-0 items-center gap-1">
          <button
            type="button"
            aria-expanded={expanded}
            aria-label={`${team.team.name}: oldest items and load per owner`}
            onClick={onToggle}
            className="flex size-6 shrink-0 cursor-pointer items-center justify-center rounded-chip hover:bg-dispatch-wash"
          >
            <Chevron className="size-4" strokeWidth={1.75} aria-hidden />
          </button>
          <Link
            to={queueUrl({ team: key, status: OPEN })}
            className="truncate text-body font-strong no-underline hover:underline"
          >
            {team.team.name}
          </Link>
          <span className="tnum text-meta text-pencil">{team.open}</span>
        </div>
        <div role="cell">
          <PriorityBar team={team} scale={scale} />
        </div>
        <div role="cell" className="text-right">
          {status("new", "new")}
        </div>
        <div role="cell" className="text-right">
          {status("in_progress", "in progress")}
        </div>
        <div role="cell" className="text-right">
          {status("blocked", "blocked")}
        </div>
        <div role="cell" className="text-right">
          {status("awaiting_approval", "awaiting approval")}
        </div>
        <div role="cell" className="text-right">
          <Figure
            value={team.unowned}
            to={queueUrl({ team: key, status: OPEN, assignee: "none" })}
            label={`unowned in ${team.team.name}`}
          />
        </div>
        <div role="cell" className="text-right">
          <Figure
            value={team.overdue}
            to={queueUrl({ team: key, overdue: true })}
            label={`overdue in ${team.team.name}`}
          />
        </div>
        <div role="cell" className="tnum text-right text-body text-pencil">
          <span title="In progress or blocked with no activity for 72 hours">
            {team.going_quiet}
          </span>
        </div>
      </div>
      {expanded && (
        <div role="row" className="border-b border-rule bg-desk px-4 py-4">
          <div role="cell" className="grid gap-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
            <section aria-label={`Oldest open items in ${team.team.name}`}>
              <h3 className="mb-2 text-section">Oldest open</h3>
              {team.oldest.length === 0 ? (
                <p className="text-meta text-pencil">Nothing open that you can see.</p>
              ) : (
                <ul className="m-0 list-none p-0">
                  {team.oldest.map((item) => (
                    <li key={item.key} className="flex items-center gap-3 py-1">
                      <StatusIcon status={item.status} />
                      <Link
                        to={`/items/${item.key}`}
                        className="tnum text-meta text-pencil no-underline hover:underline"
                      >
                        {item.key}
                      </Link>
                      <span className="min-w-0 flex-1 truncate text-body">{item.title}</span>
                      <span className="tnum text-meta text-pencil">
                        {PRIORITY_LABEL[item.priority]}
                      </span>
                      <time
                        dateTime={item.created_at}
                        title={fullTime(item.created_at)}
                        className="tnum w-10 text-right text-meta text-pencil"
                      >
                        {ageShort(item.created_at, now)}
                      </time>
                    </li>
                  ))}
                </ul>
              )}
            </section>
            <section aria-label={`Load per owner in ${team.team.name}`}>
              <h3 className="mb-2 text-section">Load per owner</h3>
              {team.owners.length === 0 ? (
                <p className="text-meta text-pencil">Nobody owns an open item here.</p>
              ) : (
                <ul className="m-0 list-none p-0">
                  {team.owners.map((owner) => (
                    <li key={owner.user.id} className="flex items-center gap-2 py-1">
                      <Avatar person={owner.user} size={20} />
                      <span className="min-w-0 flex-1 truncate text-body">{owner.user.name}</span>
                      <Link
                        to={queueUrl({ status: OPEN, assignee: owner.user.id, team: key })}
                        className="tnum text-body no-underline hover:underline"
                        aria-label={`${plural(owner.open, "open item", "open items")} owned by ${owner.user.name}`}
                      >
                        {owner.open}
                      </Link>
                    </li>
                  ))}
                  {team.owners_total > team.owners.length && (
                    <li className="pt-1 text-meta text-pencil">
                      and {team.owners_total - team.owners.length} more
                    </li>
                  )}
                </ul>
              )}
            </section>
          </div>
        </div>
      )}
    </Fragment>
  );
}

function DashboardSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading the dashboard" className="px-6 py-6">
      <div className="mb-8 flex gap-12">
        {[0, 1, 2, 3, 4].map((i) => (
          <div key={i} className="flex flex-col gap-2">
            <Skeleton className="h-9 w-16" />
            <Skeleton className="h-3 w-24" />
          </div>
        ))}
      </div>
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex h-12 items-center gap-6 border-b border-rule">
          <Skeleton className="h-4 w-32" />
          <Skeleton className="h-2 flex-1" />
          <Skeleton className="h-4 w-40" />
        </div>
      ))}
    </div>
  );
}

export function DashboardPage() {
  const stats = useTeamStats();
  const now = useNow();
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  if (stats.isPending) return <DashboardSkeleton />;
  if (stats.isError) {
    return (
      <ErrorState
        title="Couldn't load the dashboard."
        error={stats.error}
        onRetry={() => void stats.refetch()}
      />
    );
  }

  const { org, teams } = stats.data;
  const scale = Math.max(0, ...teams.map((t) => t.open));

  return (
    <div className="h-full overflow-auto">
      <div className="min-w-[1000px] pb-8">
        <header className="px-6 pb-2 pt-6">
          <h1 className="text-title">Dashboard</h1>
          <p className="text-meta text-pencil">
            Open requests in the teams you can see. Figures follow your access, so a confidential
            request counts only for people who may view it.
          </p>
        </header>

        {teams.length === 0 ? (
          <EmptyState title="No teams to show yet.">
            Join a team or raise a request, and its numbers will appear here.
          </EmptyState>
        ) : (
          <>
            <div
              className="flex flex-wrap gap-x-12 gap-y-4 px-6 py-6"
              role="group"
              aria-label="Whole organisation"
            >
              <Headline value={org.open} label="Open" to={queueUrl({ status: OPEN })} />
              <Headline
                value={org.overdue}
                label="Overdue"
                to={queueUrl({ overdue: true })}
                tone="alert"
              />
              <Headline
                value={org.unowned}
                label="Unowned"
                to={queueUrl({ status: OPEN, assignee: "none" })}
              />
              <Headline
                value={org.awaiting_approval}
                label="Awaiting approval"
                to={queueUrl({ status: ["awaiting_approval"] })}
              />
              <Headline value={org.going_quiet} label="Going quiet" />
            </div>

            <div role="table" aria-label="Open requests by team" className="border-t border-rule">
              <div role="rowgroup">
                <div
                  role="row"
                  className={cn(
                    COLS,
                    "h-9 border-b border-rule px-4 text-small font-strong text-pencil",
                  )}
                >
                  <div role="columnheader">Team</div>
                  <div role="columnheader">Open by priority</div>
                  {HEADERS.map((h) => (
                    <div key={h} role="columnheader" className="text-right">
                      {h}
                    </div>
                  ))}
                </div>
              </div>
              <div role="rowgroup">
                {teams.map((team) => (
                  <TeamRow
                    key={team.team.key}
                    team={team}
                    scale={scale}
                    now={now}
                    expanded={expanded[team.team.key] === true}
                    onToggle={() => {
                      setExpanded((e) => ({ ...e, [team.team.key]: !e[team.team.key] }));
                    }}
                  />
                ))}
              </div>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
