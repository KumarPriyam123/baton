import { useQueryClient } from "@tanstack/react-query";
import {
  ChevronsUpDown,
  Gavel,
  Inbox,
  LayoutDashboard,
  ListChecks,
  Moon,
  Send,
  Sun,
  SunMoon,
  Timer,
  Users,
} from "lucide-react";
import { useState } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router";
import { toast } from "sonner";

import { useAttention, useFacets, useMe, useTeams } from "../../api/queries";
import { Avatar } from "../../components/ui/Avatar";
import { cn } from "../../components/ui/cn";
import {
  MenuContent,
  MenuItem,
  MenuLabel,
  MenuRadio,
  MenuRadioGroup,
  MenuRoot,
  MenuSeparator,
  MenuTrigger,
} from "../../components/ui/Menu";
import { NotificationBell } from "../../features/notifications/NotificationBell";
import { api } from "../../lib/api";
import { OPEN_STATUSES, type ListFilters, emptyFilters, serializeFilters } from "../../lib/filters";
import { type Theme, applyTheme, getTheme } from "../../lib/theme";

const INBOX_CAP = 100;

export function queueLink(filters: Partial<ListFilters>): string {
  const query = serializeFilters({ ...emptyFilters(), ...filters }).toString();
  return query ? `/items?${query}` : "/items";
}

function facetTotal(counts: Record<string, number> | undefined): number | undefined {
  return counts ? Object.values(counts).reduce((a, b) => a + b, 0) : undefined;
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex h-9 items-center gap-3 rounded-chip px-2 text-body text-ink no-underline",
    "hover:bg-dispatch-wash",
    isActive && "bg-dispatch-wash font-strong",
  );

function Count({ value, cap = false }: { value: number | undefined; cap?: boolean }) {
  if (!value) return null;
  return (
    <span className="tnum ml-auto hidden text-meta text-pencil lg:inline">
      {cap && value >= INBOX_CAP ? `${String(INBOX_CAP)}+` : value}
    </span>
  );
}

export function Rail() {
  const location = useLocation();
  const attention = useAttention();
  const teams = useTeams();
  const isAdmin = useMe().data?.user.is_admin ?? false;

  const open = [...OPEN_STATUSES];
  const myWork = useFacets({ ...emptyFilters(), assignee: "me", status: open });
  const myRequests = useFacets({ ...emptyFilters(), requester: "me" });

  const inboxCount = attention.data?.sections.reduce((n, s) => n + s.count, 0);
  const myTeams = teams.data?.filter((t) => t.my_role !== null) ?? [];
  const shownTeams = myTeams.length > 0 ? myTeams : (teams.data ?? []);

  // "My work" and the team views are presets of the queue's filters (DESIGN §4.3).
  const onQueue = location.pathname.startsWith("/items");
  const activeUrl = location.pathname + location.search;
  const presetClass = (to: string) => cn(linkClass({ isActive: onQueue && activeUrl === to }));

  return (
    <nav
      aria-label="Main"
      className="flex min-h-0 flex-col gap-1 overflow-y-auto bg-desk px-2 py-4 lg:px-3"
    >
      <Link
        to="/inbox"
        className="mb-3 flex items-center gap-2 px-2 text-section font-heading text-ink no-underline"
      >
        <Send className="size-4 text-dispatch" strokeWidth={1.75} aria-hidden />
        <span className="hidden lg:inline">Baton</span>
      </Link>

      <NavLink to="/inbox" className={linkClass} title="Inbox">
        <Inbox className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
        <span className="hidden lg:inline">Inbox</span>
        <Count value={inboxCount} cap />
      </NavLink>
      <Link
        to={queueLink({ assignee: "me", status: open })}
        className={presetClass(queueLink({ assignee: "me", status: open }))}
        title="My work"
      >
        <ListChecks className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
        <span className="hidden lg:inline">My work</span>
        <Count value={facetTotal(myWork.data?.status)} />
      </Link>
      <Link
        to={queueLink({ requester: "me" })}
        className={presetClass(queueLink({ requester: "me" }))}
        title="My requests"
      >
        <Send className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
        <span className="hidden lg:inline">My requests</span>
        <Count value={facetTotal(myRequests.data?.status)} />
      </Link>

      {shownTeams.length > 0 && (
        <div className="mt-4 flex flex-col gap-1">
          <h2 className="hidden px-2 pb-1 text-meta font-strong text-pencil lg:block">Teams</h2>
          {shownTeams.map((team) => {
            const to = queueLink({ team: team.key, status: open });
            return (
              <Link key={team.key} to={to} className={presetClass(to)} title={team.name}>
                <Users className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
                <span className="hidden truncate lg:inline">{team.name}</span>
              </Link>
            );
          })}
        </div>
      )}

      <div className="mt-4 flex flex-col gap-1">
        <h2 className="hidden px-2 pb-1 text-meta font-strong text-pencil lg:block">Overview</h2>
        <NavLink to="/dashboard" className={linkClass} title="Dashboard">
          <LayoutDashboard className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
          <span className="hidden lg:inline">Dashboard</span>
        </NavLink>
        <NavLink to="/decisions" className={linkClass} title="Decisions">
          <Gavel className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
          <span className="hidden lg:inline">Decisions</span>
        </NavLink>
        {isAdmin && (
          <NavLink to="/admin/jobs" className={linkClass} title="Jobs">
            <Timer className="size-4 shrink-0" strokeWidth={1.75} aria-hidden />
            <span className="hidden lg:inline">Jobs</span>
          </NavLink>
        )}
      </div>

      <div className="mt-auto flex flex-col gap-1 pt-4">
        <NotificationBell />
        <UserMenu />
      </div>
    </nav>
  );
}

function UserMenu() {
  const me = useMe();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [theme, setTheme] = useState<Theme>(getTheme);

  const signOut = async () => {
    try {
      await api.POST("/api/v1/auth/logout");
    } catch {
      toast.error("Couldn't sign out. Check your connection and try again.");
      return;
    }
    qc.clear();
    void navigate("/login", { replace: true });
  };

  const user = me.data?.user;
  return (
    <MenuRoot>
      <MenuTrigger
        className="flex h-11 w-full cursor-pointer items-center gap-2 rounded-chip px-2 text-left hover:bg-dispatch-wash"
        aria-label="User menu"
      >
        {user && <Avatar person={{ id: user.id, name: user.name }} />}
        <span className="hidden min-w-0 flex-1 truncate text-body lg:inline">{user?.name}</span>
        <ChevronsUpDown
          className="hidden size-4 text-pencil lg:block"
          strokeWidth={1.75}
          aria-hidden
        />
      </MenuTrigger>
      <MenuContent align="start" className="min-w-56">
        <MenuLabel>{user?.email}</MenuLabel>
        <MenuSeparator />
        <MenuLabel>Theme</MenuLabel>
        <MenuRadioGroup
          value={theme}
          onValueChange={(value) => {
            const next = value as Theme;
            setTheme(next);
            applyTheme(next);
          }}
        >
          <MenuRadio value="system">
            <SunMoon className="size-4" strokeWidth={1.75} aria-hidden /> Match system
          </MenuRadio>
          <MenuRadio value="light">
            <Sun className="size-4" strokeWidth={1.75} aria-hidden /> Light
          </MenuRadio>
          <MenuRadio value="dark">
            <Moon className="size-4" strokeWidth={1.75} aria-hidden /> Dark
          </MenuRadio>
        </MenuRadioGroup>
        <MenuSeparator />
        <MenuItem onSelect={() => void signOut()}>Sign out</MenuItem>
      </MenuContent>
    </MenuRoot>
  );
}
