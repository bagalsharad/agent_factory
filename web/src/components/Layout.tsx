import { NavLink, Outlet } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import { cx } from "./ui";

const NAV = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/agents", label: "Agent Catalog" },
  { to: "/designer", label: "Agent Designer" },
  { to: "/executions", label: "Execution Monitor" },
  { to: "/approvals", label: "Approvals", badge: true },
  { to: "/evaluations", label: "Evaluation Lab" },
  { to: "/models", label: "Models & Providers" },
  { to: "/catalog", label: "Tools & Templates" },
  { to: "/knowledge", label: "Knowledge" },
  { to: "/settings", label: "Settings" },
  { to: "/audit", label: "Audit Log" },
];

export default function Layout() {
  const { me, authError } = useSession();
  const { data: pending } = useApi<{ total: number }>(authError ? null : "/api/v1/approvals?status=pending&limit=1", { interval: 5000 });
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-slate-200 bg-white px-3 py-4 md:flex dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-6 flex items-center gap-2 px-2">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-indigo-600 text-sm font-bold text-white">AF</div>
          <div>
            <div className="text-sm font-semibold">Agent Factory</div>
            <div className="text-[11px] text-slate-500">Design · Build · Test · Govern</div>
          </div>
        </div>
        <nav className="flex flex-1 flex-col gap-0.5">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end}
              className={({ isActive }) => cx("flex items-center justify-between rounded-lg px-2.5 py-1.5 text-sm",
                isActive ? "bg-indigo-50 font-medium text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300"
                  : "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800")}>
              {item.label}
              {item.badge && pending && pending.total > 0 && (
                <span className="rounded-full bg-amber-500 px-1.5 text-[11px] font-semibold text-white">{pending.total}</span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-4 rounded-lg bg-slate-50 p-2.5 text-xs text-slate-500 dark:bg-slate-800/60 dark:text-slate-400">
          {me ? (
            <>
              <div className="font-medium text-slate-700 dark:text-slate-200">{me.display_name}</div>
              <div>{me.role} · {me.workspace?.name}</div>
              <div className="mt-0.5 opacity-80">auth: {me.auth_method}</div>
            </>
          ) : authError ? "Not signed in — add an API key in Settings" : "…"}
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center gap-3 overflow-x-auto border-b border-slate-200 bg-white px-4 py-2 md:hidden dark:border-slate-800 dark:bg-slate-900">
          {NAV.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => cx("whitespace-nowrap text-sm", isActive ? "font-semibold text-indigo-600" : "text-slate-600")}>
              {item.label}
            </NavLink>
          ))}
        </header>
        <main className="mx-auto w-full max-w-7xl flex-1 px-4 py-6 sm:px-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
