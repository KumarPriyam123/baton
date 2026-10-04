/** Below 768 px the rail becomes a bottom bar (DESIGN §3.2): the four places people go all day. */
import { Gavel, Inbox, LayoutDashboard, ListChecks } from "lucide-react";
import { NavLink } from "react-router";

import { cn } from "../../components/ui/cn";
import { NotificationBell } from "../../features/notifications/NotificationBell";
import { queueLink, UserMenu } from "./Rail";
import { OPEN_STATUSES } from "../../lib/filters";

const link = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex min-h-14 flex-1 flex-col items-center justify-center gap-0.5 text-small text-pencil no-underline",
    isActive && "font-strong text-dispatch",
  );

export function MobileNav() {
  const myWork = queueLink({ assignee: "me", status: [...OPEN_STATUSES] });
  return (
    <nav
      aria-label="Main"
      className="flex shrink-0 items-stretch border-t border-rule bg-desk px-1 md:hidden"
    >
      <NavLink to="/inbox" className={link}>
        <Inbox className="size-5" strokeWidth={1.75} aria-hidden />
        Inbox
      </NavLink>
      <NavLink to={myWork} className={link}>
        <ListChecks className="size-5" strokeWidth={1.75} aria-hidden />
        My work
      </NavLink>
      <NavLink to="/dashboard" className={link}>
        <LayoutDashboard className="size-5" strokeWidth={1.75} aria-hidden />
        Dashboard
      </NavLink>
      <NavLink to="/decisions" className={link}>
        <Gavel className="size-5" strokeWidth={1.75} aria-hidden />
        Decisions
      </NavLink>
      <div className="flex w-11 shrink-0 items-center justify-center">
        <NotificationBell />
      </div>
      <div className="flex w-11 shrink-0 items-center justify-center">
        <UserMenu />
      </div>
    </nav>
  );
}
