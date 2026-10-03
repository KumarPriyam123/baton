/** Filter bar (DESIGN §4.3): removable tokens, an Add filter menu with facet counts, a sort menu. */
import { ArrowDownUp, ListFilter } from "lucide-react";

import { useFacets } from "../../api/queries";
import { Button } from "../../components/ui/Button";
import { FilterToken } from "../../components/ui/FilterToken";
import {
  MenuCheckbox,
  MenuContent,
  MenuLabel,
  MenuRadio,
  MenuRadioGroup,
  MenuRoot,
  MenuSeparator,
  MenuTrigger,
} from "../../components/ui/Menu";
import {
  type FilterPatch,
  type ListFilters,
  type Sort,
  applyPatch,
  OPEN_STATUSES,
  STATUSES,
  TYPES,
  isOpenSet,
} from "../../lib/filters";
import { PRIORITY_LABEL, STATUS_LABEL, TYPE_LABEL } from "../../lib/format";

const SORT_LABEL: Record<Sort, string> = {
  priority: "Priority",
  updated: "Recently updated",
  created: "Newest",
  due: "Due soonest",
};

function toggle<T>(list: T[], value: T, on: boolean): T[] {
  return on ? [...list.filter((v) => v !== value), value] : list.filter((v) => v !== value);
}

function priorityLabel(priority: number[]): string {
  const sorted = [...priority].sort((a, b) => a - b);
  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const contiguous = sorted.every((p, i) => i === 0 || p === (sorted[i - 1] ?? 0) + 1);
  if (sorted.length > 2 && contiguous && first !== undefined && last !== undefined) {
    return `${PRIORITY_LABEL[first] ?? ""}–${PRIORITY_LABEL[last] ?? ""}`;
  }
  return sorted.map((p) => PRIORITY_LABEL[p] ?? "").join(", ");
}

/** The tokens currently narrowing the list, as [label, how to remove it]. */
function tokens(
  filters: ListFilters,
  teamName: (key: string) => string,
): { id: string; label: string; remove: FilterPatch }[] {
  const out: { id: string; label: string; remove: FilterPatch }[] = [];
  if (filters.team) {
    out.push({ id: "team", label: `Team: ${teamName(filters.team)}`, remove: { team: undefined } });
  }
  if (filters.status.length) {
    out.push({
      id: "status",
      label: isOpenSet(filters.status)
        ? "Status: Open"
        : `Status: ${filters.status.map((s) => STATUS_LABEL[s]).join(", ")}`,
      remove: { status: [] },
    });
  }
  if (filters.priority.length) {
    out.push({
      id: "priority",
      label: `Priority: ${priorityLabel(filters.priority)}`,
      remove: { priority: [] },
    });
  }
  if (filters.type.length) {
    out.push({
      id: "type",
      label: `Type: ${filters.type.map((t) => TYPE_LABEL[t]).join(", ")}`,
      remove: { type: [] },
    });
  }
  if (filters.assignee) {
    const who =
      filters.assignee === "me" ? "Me" : filters.assignee === "none" ? "Nobody" : "Someone";
    out.push({ id: "assignee", label: `Assignee: ${who}`, remove: { assignee: undefined } });
  }
  if (filters.requester) {
    out.push({
      id: "requester",
      label: `Requester: ${filters.requester === "me" ? "Me" : "Someone"}`,
      remove: { requester: undefined },
    });
  }
  if (filters.overdue)
    out.push({ id: "overdue", label: "Overdue", remove: { overdue: undefined } });
  if (filters.q) out.push({ id: "q", label: `Search: “${filters.q}”`, remove: { q: undefined } });
  return out;
}

export function FilterBar({
  filters,
  teams,
  onChange,
}: {
  filters: ListFilters;
  teams: { key: string; name: string }[];
  onChange: (next: ListFilters) => void;
}) {
  // Counts for the same filters, so each option says how many items it would show.
  const facets = useFacets(filters);
  const counts = facets.data;
  const teamName = (key: string) => teams.find((t) => t.key === key)?.name ?? key;
  const apply = (patch: FilterPatch) => {
    onChange(applyPatch(filters, patch));
  };
  const active = tokens(filters, teamName);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b border-rule px-4 py-2">
      {active.map((token) => (
        <FilterToken
          key={token.id}
          label={token.label}
          onRemove={() => {
            apply(token.remove);
          }}
        />
      ))}

      <MenuRoot>
        <MenuTrigger asChild>
          <Button variant="quiet" className="h-7 px-2 text-meta">
            <ListFilter className="size-4" strokeWidth={1.75} aria-hidden />
            Add filter
          </Button>
        </MenuTrigger>
        <MenuContent className="max-h-[70dvh] overflow-y-auto">
          <MenuLabel>Status</MenuLabel>
          <MenuCheckbox
            checked={isOpenSet(filters.status)}
            onCheckedChange={(on) => {
              apply({ status: on ? [...OPEN_STATUSES] : [] });
            }}
          >
            Open
          </MenuCheckbox>
          {STATUSES.map((status) => (
            <MenuCheckbox
              key={status}
              checked={filters.status.includes(status)}
              count={counts?.status[status] ?? 0}
              onCheckedChange={(on) => {
                apply({ status: toggle(filters.status, status, on) });
              }}
            >
              {STATUS_LABEL[status]}
            </MenuCheckbox>
          ))}
          <MenuSeparator />
          <MenuLabel>Priority</MenuLabel>
          {PRIORITY_LABEL.map((label, priority) => (
            <MenuCheckbox
              key={label}
              checked={filters.priority.includes(priority)}
              count={counts?.priority[String(priority)] ?? 0}
              onCheckedChange={(on) => {
                apply({ priority: toggle(filters.priority, priority, on) });
              }}
            >
              {label}
            </MenuCheckbox>
          ))}
          <MenuSeparator />
          <MenuLabel>Type</MenuLabel>
          {TYPES.map((type) => (
            <MenuCheckbox
              key={type}
              checked={filters.type.includes(type)}
              count={counts?.type[type] ?? 0}
              onCheckedChange={(on) => {
                apply({ type: toggle(filters.type, type, on) });
              }}
            >
              {TYPE_LABEL[type]}
            </MenuCheckbox>
          ))}
          <MenuSeparator />
          <MenuLabel>Team</MenuLabel>
          {teams.map((team) => (
            <MenuCheckbox
              key={team.key}
              checked={filters.team === team.key}
              count={counts?.team[team.key] ?? 0}
              onCheckedChange={(on) => {
                apply({ team: on ? team.key : undefined });
              }}
            >
              {team.name}
            </MenuCheckbox>
          ))}
          <MenuSeparator />
          <MenuLabel>Assignee</MenuLabel>
          <MenuCheckbox
            checked={filters.assignee === "me"}
            onCheckedChange={(on) => {
              apply({ assignee: on ? "me" : undefined });
            }}
          >
            Assigned to me
          </MenuCheckbox>
          <MenuCheckbox
            checked={filters.assignee === "none"}
            onCheckedChange={(on) => {
              apply({ assignee: on ? "none" : undefined });
            }}
          >
            Unassigned
          </MenuCheckbox>
          <MenuCheckbox
            checked={filters.requester === "me"}
            onCheckedChange={(on) => {
              apply({ requester: on ? "me" : undefined });
            }}
          >
            Raised by me
          </MenuCheckbox>
          <MenuCheckbox
            checked={filters.overdue === true}
            onCheckedChange={(on) => {
              apply({ overdue: on ? true : undefined });
            }}
          >
            Overdue
          </MenuCheckbox>
        </MenuContent>
      </MenuRoot>

      <div className="ml-auto">
        <MenuRoot>
          <MenuTrigger asChild>
            <Button
              variant="quiet"
              className="h-7 px-2 text-meta"
              disabled={filters.q !== undefined}
              title={filters.q ? "Search results are ranked by relevance" : undefined}
            >
              <ArrowDownUp className="size-4" strokeWidth={1.75} aria-hidden />
              {filters.q ? "Best match" : `Sort: ${SORT_LABEL[filters.sort]}`}
            </Button>
          </MenuTrigger>
          <MenuContent align="end">
            <MenuRadioGroup
              value={filters.sort}
              onValueChange={(value) => {
                apply({ sort: value as Sort });
              }}
            >
              {(Object.keys(SORT_LABEL) as Sort[]).map((sort) => (
                <MenuRadio key={sort} value={sort}>
                  {SORT_LABEL[sort]}
                </MenuRadio>
              ))}
            </MenuRadioGroup>
          </MenuContent>
        </MenuRoot>
      </div>
    </div>
  );
}
