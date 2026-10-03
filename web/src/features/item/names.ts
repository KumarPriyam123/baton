/**
 * Event data names people and teams by id. The names come from what the client already has:
 * the item's people, event actors, the team's members (first page) and the team list.
 * Someone who left the team and never acted on this item shows as "someone" (known limit).
 */
import { useMemo } from "react";

import { useMembers, useTeams } from "../../api/queries";
import type { ItemOut } from "../../lib/api";
import type { EventOut, NameLookup } from "../../lib/eventText";
import type { Person } from "./track";

export function useNames(
  item: ItemOut,
  events: readonly EventOut[],
): NameLookup & {
  resolve: (id: string) => Person;
  members: readonly Person[];
} {
  const members = useMembers(item.team.key);
  const teams = useTeams();

  return useMemo(() => {
    const people = new Map<string, string>();
    const add = (person: { id: string; name: string } | null | undefined) => {
      if (person) people.set(person.id, person.name);
    };
    for (const member of members.data?.items ?? []) add(member.user);
    for (const event of events) add(event.actor);
    add(item.requester);
    add(item.assignee);

    const teamNames = new Map<string, string>();
    for (const team of teams.data ?? []) teamNames.set(team.id, team.name);

    const person = (id: unknown): string =>
      (typeof id === "string" ? people.get(id) : undefined) ?? "someone";
    const team = (id: unknown): string =>
      (typeof id === "string" ? teamNames.get(id) : undefined) ?? "another team";
    return {
      person,
      team,
      resolve: (id: string): Person => ({ id, name: person(id) }),
      members: (members.data?.items ?? []).map((m) => ({ id: m.user.id, name: m.user.name })),
    };
  }, [members.data, teams.data, events, item.requester, item.assignee]);
}
