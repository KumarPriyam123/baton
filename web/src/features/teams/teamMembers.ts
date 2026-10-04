/**
 * Team members (SPEC §4.3b, §11): the list, and the three commands on it. Who may use them is
 * decided by the server; `memberControls` only decides which controls are worth drawing, so a
 * plain member isn't shown buttons that can only answer 403.
 */
import {
  keepPreviousData,
  useInfiniteQuery,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import type { components } from "../../api/generated";
import { api, unwrap } from "../../lib/api";
import { OPEN_STATUSES, emptyFilters, toFacetQuery } from "../../lib/filters";
import { useCommand } from "../../lib/useCommand";
import { keys } from "../../api/keys";

export type Member = components["schemas"]["MemberOut"];
export type TeamRole = components["schemas"]["TeamRole"];

export const ROLE_LABEL: Record<TeamRole, string> = {
  lead: "Lead",
  member: "Member",
  viewer: "Viewer",
};

const PAGE = 50;

export const teamMembersKey = (teamKey: string) => ["teams", teamKey, "members", "all"] as const;

export function useTeamMembers(teamKey: string) {
  return useInfiniteQuery({
    queryKey: teamMembersKey(teamKey),
    queryFn: ({ pageParam }) =>
      unwrap(
        api.GET("/api/v1/teams/{key}/members", {
          params: {
            path: { key: teamKey },
            query: { limit: PAGE, ...(pageParam ? { cursor: pageParam } : {}) },
          },
        }),
      ),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/** People who could be added: the directory, matched by name or email (SPEC §11). */
export function useDirectory(q: string) {
  return useQuery({
    queryKey: ["users", q],
    queryFn: () => unwrap(api.GET("/api/v1/users", { params: { query: { q, limit: 8 } } })),
    enabled: q.length >= 2,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

export interface MemberControls {
  /** Add people and change or remove members and viewers. */
  manage: boolean;
  /** Make, change or remove leads. */
  manageLeads: boolean;
}

/** SPEC §5.2: leads manage members and viewers; only admins manage leads. */
export function memberControls(myRole: TeamRole | null, isAdmin: boolean): MemberControls {
  return { manage: isAdmin || myRole === "lead", manageLeads: isAdmin };
}

/** Can these controls act on this member? A lead can't touch another lead. */
export function canEditMember(controls: MemberControls, member: Member): boolean {
  return member.role === "lead" ? controls.manageLeads : controls.manage;
}

/** Roles the person using the controls may hand out. */
export function assignableRoles(controls: MemberControls): TeamRole[] {
  return controls.manageLeads ? ["viewer", "member", "lead"] : ["viewer", "member"];
}

/**
 * The server's rule (SPEC §4.3b): removing someone, or demoting them to viewer, sends their open
 * items in this team back to "Needs an owner". The API answers 204 with no count, so the dialog
 * asks the server first: how many open items does this person own here, as this viewer sees them.
 */
export function useOwnedOpenItems(teamKey: string, userId: string | null) {
  const filters = {
    ...emptyFilters(),
    team: teamKey,
    assignee: userId ?? "none",
    status: [...OPEN_STATUSES],
  };
  // Never cached: a stale count in a confirmation would be worse than a short spinner.
  const facets = useQuery({
    queryKey: keys.facets(filters),
    queryFn: () =>
      unwrap(api.GET("/api/v1/items/facets", { params: { query: toFacetQuery(filters) } })),
    enabled: userId !== null,
    gcTime: 0,
  });
  const count = facets.data ? Object.values(facets.data.status).reduce((a, b) => a + b, 0) : null;
  return { count, isPending: userId !== null && facets.isPending, isError: facets.isError };
}

type Change =
  | { kind: "add"; userId: string; name: string; role: TeamRole }
  | { kind: "role"; userId: string; name: string; role: TeamRole }
  | { kind: "remove"; userId: string; name: string };

export function useMemberCommand(teamKey: string) {
  const qc = useQueryClient();
  const command = useCommand<Change, Member | null>({
    run: async (change) => {
      const path = { key: teamKey };
      switch (change.kind) {
        case "add":
          return unwrap(
            api.POST("/api/v1/teams/{key}/members", {
              params: { path },
              body: { user_id: change.userId, role: change.role },
            }),
          );
        case "role":
          return unwrap(
            api.PATCH("/api/v1/teams/{key}/members/{user_id}", {
              params: { path: { ...path, user_id: change.userId } },
              body: { role: change.role },
            }),
          );
        case "remove":
          await api.DELETE("/api/v1/teams/{key}/members/{user_id}", {
            params: { path: { ...path, user_id: change.userId } },
          });
          return null;
      }
    },
  });
  const execute = async (change: Change): Promise<boolean> => {
    try {
      await command.execute(change);
    } catch {
      return false; // useCommand toasted the server's reason (a 403 says who may do this)
    }
    void qc.invalidateQueries({ queryKey: ["teams", teamKey, "members"] });
    void qc.invalidateQueries({ queryKey: ["teams"] });
    return true;
  };
  return { execute };
}
