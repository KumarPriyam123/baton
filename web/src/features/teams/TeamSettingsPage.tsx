/**
 * Team settings (DESIGN §4.8): members, roles, add and remove. Controls are drawn only for the
 * people the server allows (leads: members and viewers; admins: leads too), and every change that
 * sends open items back to "Needs an owner" (removal, demotion to viewer) says how many first.
 */
import { useState } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";

import { useMe, useTeams } from "../../api/queries";
import { Avatar } from "../../components/ui/Avatar";
import { Button } from "../../components/ui/Button";
import { DialogPanel, DialogRoot } from "../../components/ui/Dialog";
import { Input } from "../../components/ui/Field";
import { EmptyState, ErrorState, Skeleton } from "../../components/ui/States";
import { plural } from "../../lib/format";
import {
  type Member,
  ROLE_LABEL,
  type TeamRole,
  assignableRoles,
  canEditMember,
  memberControls,
  useDirectory,
  useMemberCommand,
  useOwnedOpenItems,
  useTeamMembers,
} from "./teamMembers";

const selectClass = "h-9 rounded-chip border border-rule bg-sheet px-2 text-body text-ink";

/** A change waiting for the lead to confirm: it will hand open items back. */
type Pending =
  { kind: "remove"; member: Member } | { kind: "role"; member: Member; role: TeamRole };

export function TeamSettingsPage() {
  const { key = "" } = useParams();
  const teamKey = key.toUpperCase();
  const me = useMe();
  const teams = useTeams();
  const team = teams.data?.find((t) => t.key === teamKey);
  const members = useTeamMembers(teamKey);
  const commands = useMemberCommand(teamKey);
  const [pending, setPending] = useState<Pending | null>(null);

  if (teams.isPending || me.isPending) return <Loading />;
  if (teams.isError) {
    return (
      <ErrorState
        title="Couldn't load this team."
        error={teams.error}
        onRetry={() => void teams.refetch()}
      />
    );
  }
  if (!team) {
    return (
      <EmptyState
        title="This team doesn't exist."
        action={
          <Link to="/inbox" className="text-body text-dispatch">
            Go to your inbox
          </Link>
        }
      >
        Check the address, or pick a team from the rail.
      </EmptyState>
    );
  }

  const controls = memberControls(team.my_role, me.data?.user.is_admin ?? false);
  const rows = members.data?.pages.flatMap((p) => p.items) ?? [];

  const changeRole = (member: Member, role: TeamRole) => {
    if (role === member.role) return;
    if (role === "viewer")
      setPending({ kind: "role", member, role }); // viewers can't hold items
    else
      void commands
        .execute({ kind: "role", userId: member.user.id, name: member.user.name, role })
        .then((ok) => {
          if (ok) toast.success(`${member.user.name} is now ${ROLE_LABEL[role].toLowerCase()}`);
        });
  };

  return (
    <div className="h-full overflow-y-auto">
      <header className="px-6 pb-4 pt-6">
        <h1 className="text-title">{team.name} settings</h1>
        <p className="text-meta text-pencil">
          {controls.manage
            ? "Who is on this team and what they can do."
            : "Who is on this team. Leads and admins change this list."}
        </p>
      </header>

      <section aria-labelledby="members-heading" className="border-t border-rule">
        <h2 id="members-heading" className="px-6 pb-2 pt-4 text-section">
          Members
        </h2>
        {members.isPending ? (
          <Loading />
        ) : members.isError ? (
          <ErrorState
            title="Couldn't load members."
            error={members.error}
            onRetry={() => void members.refetch()}
          />
        ) : rows.length === 0 ? (
          <EmptyState title="No one is on this team yet." />
        ) : (
          <table className="w-full border-collapse text-body">
            <thead>
              <tr className="border-b border-rule text-left text-meta text-pencil">
                <th scope="col" className="px-6 py-2 font-strong">
                  Name
                </th>
                <th scope="col" className="px-3 py-2 font-strong">
                  Email
                </th>
                <th scope="col" className="w-40 px-3 py-2 font-strong">
                  Role
                </th>
                {controls.manage && (
                  <th scope="col" className="w-28 px-6 py-2">
                    <span className="sr-only-live">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((member) => {
                const editable = canEditMember(controls, member);
                return (
                  <tr key={member.user.id} className="border-b border-rule">
                    <td className="px-6 py-2">
                      <span className="flex items-center gap-2">
                        <Avatar person={member.user} />
                        {member.user.name}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-pencil">{member.user.email}</td>
                    <td className="px-3 py-2">
                      {editable ? (
                        <select
                          aria-label={`Role of ${member.user.name}`}
                          className={selectClass}
                          value={member.role}
                          onChange={(event) => {
                            changeRole(member, event.target.value as TeamRole);
                          }}
                        >
                          {assignableRoles(controls).map((role) => (
                            <option key={role} value={role}>
                              {ROLE_LABEL[role]}
                            </option>
                          ))}
                        </select>
                      ) : (
                        ROLE_LABEL[member.role]
                      )}
                    </td>
                    {controls.manage && (
                      <td className="px-6 py-2 text-right">
                        {editable && (
                          <Button
                            variant="quiet"
                            aria-label={`Remove ${member.user.name}`}
                            onClick={() => {
                              setPending({ kind: "remove", member });
                            }}
                          >
                            Remove
                          </Button>
                        )}
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {members.hasNextPage && (
          <div className="px-6 py-4">
            <Button
              variant="secondary"
              pending={members.isFetchingNextPage}
              onClick={() => void members.fetchNextPage()}
            >
              Show more
            </Button>
          </div>
        )}
      </section>

      {controls.manage && (
        <AddMember
          teamName={team.name}
          existing={new Set(rows.map((m) => m.user.id))}
          roles={assignableRoles(controls)}
          onAdd={(person, role) =>
            commands
              .execute({ kind: "add", userId: person.id, name: person.name, role })
              .then((ok) => {
                if (ok) toast.success(`Added ${person.name} to ${team.name}`);
                return ok;
              })
          }
        />
      )}

      <ConfirmHandback
        pending={pending}
        teamKey={teamKey}
        teamName={team.name}
        onClose={() => {
          setPending(null);
        }}
        onConfirm={async (owned) => {
          if (!pending) return;
          const { member } = pending;
          const ok = await commands.execute(
            pending.kind === "remove"
              ? { kind: "remove", userId: member.user.id, name: member.user.name }
              : {
                  kind: "role",
                  userId: member.user.id,
                  name: member.user.name,
                  role: pending.role,
                },
          );
          if (!ok) return;
          setPending(null);
          const back =
            owned > 0
              ? ` ${plural(owned, "request is", "requests are")} back in Needs an owner.`
              : "";
          toast.success(
            pending.kind === "remove"
              ? `Removed ${member.user.name} from ${team.name}.${back}`
              : `${member.user.name} is now a viewer.${back}`,
          );
        }}
      />
    </div>
  );
}

function AddMember({
  teamName,
  existing,
  roles,
  onAdd,
}: {
  teamName: string;
  existing: ReadonlySet<string>;
  roles: TeamRole[];
  onAdd: (person: { id: string; name: string }, role: TeamRole) => Promise<boolean>;
}) {
  const [text, setText] = useState("");
  const [role, setRole] = useState<TeamRole>("member");
  const [adding, setAdding] = useState<string | null>(null);
  const q = text.trim();
  const directory = useDirectory(q);
  const results = (directory.data?.items ?? []).filter((p) => !existing.has(p.id));

  return (
    <section aria-labelledby="add-heading" className="border-t border-rule px-6 py-4">
      <h2 id="add-heading" className="mb-3 text-section">
        Add to {teamName}
      </h2>
      <div className="flex flex-wrap items-end gap-3">
        <label
          htmlFor="add-member-q"
          className="flex w-full max-w-md flex-col gap-1 text-meta font-strong"
        >
          Find a person
          <Input
            id="add-member-q"
            value={text}
            placeholder="Name or email"
            onChange={(event) => {
              setText(event.target.value);
            }}
          />
        </label>
        <label className="flex flex-col gap-1 text-meta font-strong">
          Role
          <select
            className={selectClass}
            value={role}
            onChange={(event) => {
              setRole(event.target.value as TeamRole);
            }}
          >
            {roles.map((r) => (
              <option key={r} value={r}>
                {ROLE_LABEL[r]}
              </option>
            ))}
          </select>
        </label>
      </div>
      {q.length >= 2 && (
        <ul className="mt-3 max-w-2xl divide-y divide-rule rounded-panel border border-rule">
          {directory.isPending ? (
            <li className="px-3 py-3 text-meta text-pencil">Searching…</li>
          ) : results.length === 0 ? (
            <li className="px-3 py-3 text-meta text-pencil">
              No one new matches "{q}". People already on the team aren't listed.
            </li>
          ) : (
            results.map((person) => (
              <li key={person.id} className="flex items-center gap-3 px-3 py-2">
                <Avatar person={person} />
                <span className="min-w-0 flex-1">
                  <span className="block truncate">{person.name}</span>
                  <span className="block truncate text-meta text-pencil">{person.email}</span>
                </span>
                <Button
                  variant="secondary"
                  pending={adding === person.id}
                  disabled={adding !== null && adding !== person.id}
                  aria-label={`Add ${person.name} as ${ROLE_LABEL[role].toLowerCase()}`}
                  onClick={() => {
                    setAdding(person.id);
                    void onAdd(person, role).finally(() => {
                      setAdding(null);
                    });
                  }}
                >
                  Add
                </Button>
              </li>
            ))
          )}
        </ul>
      )}
    </section>
  );
}

/**
 * Asks the server how many open requests this person owns here before the change, then waits for
 * the lead to confirm. The server unassigns them in the same transaction as the change.
 */
function ConfirmHandback({
  pending,
  teamKey,
  teamName,
  onClose,
  onConfirm,
}: {
  pending: Pending | null;
  teamKey: string;
  teamName: string;
  onClose: () => void;
  onConfirm: (owned: number) => Promise<void>;
}) {
  const [working, setWorking] = useState(false);
  const owned = useOwnedOpenItems(teamKey, pending?.member.user.id ?? null);
  const name = pending?.member.user.name ?? "";
  const verb = pending?.kind === "remove" ? "Remove" : "Make viewer";
  const title = pending?.kind === "remove" ? `Remove ${name}?` : `Make ${name} a viewer?`;

  return (
    <DialogRoot
      open={pending !== null}
      onOpenChange={(open) => {
        if (!open && !working) onClose();
      }}
    >
      {pending && (
        <DialogPanel title={title} className="w-[min(480px,100%)]">
          <div className="text-body" aria-live="polite">
            {owned.isPending ? (
              <Skeleton className="h-5 w-64" />
            ) : owned.isError || owned.count === null ? (
              <p>
                Couldn't check which requests {name} owns. Anything open they own in {teamName} will
                go back to Needs an owner.
              </p>
            ) : owned.count > 0 ? (
              <p>
                {name} owns {plural(owned.count, "open request", "open requests")} in {teamName}.{" "}
                {owned.count === 1 ? "It goes" : "They go"} back to Needs an owner. Reassign{" "}
                {owned.count === 1 ? "it" : "them"} first if you'd rather hand the work to someone.
              </p>
            ) : (
              <p>
                {name} doesn't own any open requests in {teamName}, so nothing moves.
              </p>
            )}
          </div>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="quiet" onClick={onClose} disabled={working}>
              Cancel
            </Button>
            <Button
              variant={pending.kind === "remove" ? "danger" : "primary"}
              pending={working}
              disabled={owned.isPending}
              onClick={() => {
                setWorking(true);
                void onConfirm(owned.count ?? 0).finally(() => {
                  setWorking(false);
                });
              }}
            >
              {verb}
            </Button>
          </div>
        </DialogPanel>
      )}
    </DialogRoot>
  );
}

function Loading() {
  return (
    <div aria-busy="true" aria-label="Loading team" className="space-y-3 px-6 py-4">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-5 w-full" />
      ))}
    </div>
  );
}
