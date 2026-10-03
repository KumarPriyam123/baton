/** Decision log (DESIGN §4.7): who decided what, the reason as a quotation, and the item.
 * Filters live in the URL (`?team=PAY&kind=closed`), like the queue's. */
import { useSearchParams } from "react-router";

import type { components } from "../../api/generated";
import { useDecisions, useTeams } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { type EventOut, actorName, describeEvent } from "../../lib/eventText";
import { ageShort, fullTime } from "../../lib/format";
import { useNow } from "../../lib/useNow";
import { Link } from "react-router";

type Decision = components["schemas"]["DecisionOut"];

/** The kinds a person can filter by, in the words of DESIGN §4.7. */
const KINDS: { value: string; label: string }[] = [
  { value: "approval_approved", label: "Approvals" },
  { value: "approval_rejected", label: "Rejections" },
  { value: "resolved", label: "Resolutions" },
  { value: "closed", label: "Closes" },
  { value: "reopened", label: "Reopens" },
  { value: "priority_changed", label: "Priority downgrades" },
  { value: "transferred", label: "Transfers" },
  { value: "requires_approval_changed", label: "Approval requirement removed" },
  { value: "approval_invalidated", label: "Approvals withdrawn" },
  { value: "confidential_changed", label: "Confidentiality changes" },
  { value: "status_changed", label: "Other status changes" },
];

const selectClass = "h-9 rounded-chip border border-rule bg-sheet px-2 text-body text-ink";

function DecisionRow({
  decision,
  now,
  teamName,
}: {
  decision: Decision;
  now: number;
  teamName: (id: unknown) => string;
}) {
  const event: EventOut = {
    id: decision.id,
    kind: decision.kind,
    actor: decision.actor,
    item_version: 0,
    data: decision.data,
    reason: decision.reason,
    is_decision: true,
    created_at: decision.created_at,
  };
  const sentence = describeEvent(event, { person: () => "someone", team: teamName });
  return (
    <li className="flex gap-4 border-b border-rule px-6 py-4">
      <div className="min-w-0 flex-1">
        <p className="flex items-baseline gap-2">
          <Link
            to={`/items/${decision.item_key}`}
            className="tnum text-meta text-pencil no-underline hover:underline"
          >
            {decision.item_key}
          </Link>
          <Link
            to={`/items/${decision.item_key}`}
            className="truncate text-body font-strong text-ink no-underline hover:underline"
          >
            {decision.item_title}
          </Link>
        </p>
        <p className="mt-1 text-body">
          <span className="font-strong">{actorName(decision)}</span> {sentence}
        </p>
        {decision.reason ? (
          <blockquote className="m-0 mt-2 max-w-2xl border-l-2 border-rule pl-3 text-body text-ink">
            {decision.reason}
          </blockquote>
        ) : (
          <p className="mt-2 text-meta text-pencil">No reason was given.</p>
        )}
      </div>
      <div className="flex shrink-0 flex-col items-end gap-1 text-meta text-pencil">
        <span>{decision.team.name}</span>
        <time dateTime={decision.created_at} title={fullTime(decision.created_at)} className="tnum">
          {ageShort(decision.created_at, now)}
        </time>
      </div>
    </li>
  );
}

function DecisionsSkeleton() {
  return (
    <div aria-busy="true" aria-label="Loading decisions">
      {[0, 1, 2, 3].map((i) => (
        <div key={i} className="flex flex-col gap-2 border-b border-rule px-6 py-4">
          <Skeleton className="h-4 w-80" />
          <Skeleton className="h-4 w-56" />
          <Skeleton className="h-4 w-96 max-w-full" />
        </div>
      ))}
    </div>
  );
}

export function DecisionsPage() {
  const [params, setParams] = useSearchParams();
  const team = (params.get("team") ?? "").toUpperCase();
  const rawKind = params.get("kind") ?? "";
  const kind = KINDS.some((k) => k.value === rawKind) ? rawKind : "";
  const teams = useTeams();
  const decisions = useDecisions(team, kind);
  const now = useNow();

  const teamName = (id: unknown): string =>
    teams.data?.find((t) => t.id === id)?.name ?? "another team";
  const filtered = team !== "" || kind !== "";

  const setFilter = (name: "team" | "kind", value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(name, value);
    else next.delete(name);
    setParams(next, { replace: true });
  };

  const rows = decisions.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <div className="h-full overflow-y-auto">
      <header className="flex flex-wrap items-end justify-between gap-4 px-6 pb-4 pt-6">
        <div>
          <h1 className="text-title">Decisions</h1>
          <p className="text-meta text-pencil">
            Why things were approved, closed, moved or downgraded.
          </p>
        </div>
        <div className="flex gap-3">
          <label className="flex flex-col gap-1 text-small font-strong text-pencil">
            Team
            <select
              className={selectClass}
              value={team}
              onChange={(e) => {
                setFilter("team", e.target.value);
              }}
            >
              <option value="">All teams</option>
              {teams.data?.map((t) => (
                <option key={t.key} value={t.key}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-small font-strong text-pencil">
            Kind
            <select
              className={selectClass}
              value={kind}
              onChange={(e) => {
                setFilter("kind", e.target.value);
              }}
            >
              <option value="">All decisions</option>
              {KINDS.map((k) => (
                <option key={k.value} value={k.value}>
                  {k.label}
                </option>
              ))}
            </select>
          </label>
        </div>
      </header>

      <div className="border-t border-rule">
        {decisions.isPending ? (
          <DecisionsSkeleton />
        ) : decisions.isError ? (
          <ErrorState
            title="Couldn't load decisions."
            error={decisions.error}
            onRetry={() => void decisions.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState
            title={filtered ? "No decisions match these filters." : "No decisions yet."}
            action={
              filtered ? (
                <Button
                  variant="secondary"
                  onClick={() => {
                    setParams({}, { replace: true });
                  }}
                >
                  Clear filters
                </Button>
              ) : undefined
            }
          >
            {filtered
              ? "Try another team or kind."
              : "Approvals, closes, transfers and downgrades will be recorded here with their reasons."}
          </EmptyState>
        ) : (
          <>
            <ul className="m-0 list-none p-0">
              {rows.map((d) => (
                <DecisionRow key={d.id} decision={d} now={now} teamName={teamName} />
              ))}
            </ul>
            {decisions.hasNextPage && (
              <div className="px-6 py-4">
                <Button
                  variant="secondary"
                  pending={decisions.isFetchingNextPage}
                  onClick={() => void decisions.fetchNextPage()}
                >
                  Show older decisions
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
