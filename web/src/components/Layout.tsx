import { useEffect, useMemo, useRef, useState } from "react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import { getThemePreference, setThemePreference, type ThemePreference } from "../lib/theme";
import { icons, type IconName } from "./icons";
import { cx } from "./ui";

interface NavItem { to: string; label: string; icon: IconName; end?: boolean; badge?: boolean }

const GROUPS: { title: string; items: NavItem[] }[] = [
  { title: "Overview", items: [{ to: "/", label: "Dashboard", icon: "dashboard", end: true }] },
  { title: "Build", items: [
    { to: "/agents", label: "Agent Catalog", icon: "agents" },
    { to: "/designer", label: "Agent Designer", icon: "designer" },
    { to: "/catalog", label: "Tools & Templates", icon: "tools" },
    { to: "/knowledge", label: "Knowledge", icon: "knowledge" },
  ] },
  { title: "Verify & run", items: [
    { to: "/evaluations", label: "Evaluation Lab", icon: "evaluations" },
    { to: "/executions", label: "Execution Monitor", icon: "executions" },
    { to: "/approvals", label: "Approvals", icon: "approvals", badge: true },
  ] },
  { title: "Operate", items: [
    { to: "/operations", label: "Operations", icon: "operations" },
    { to: "/models", label: "Models & Providers", icon: "models" },
    { to: "/connectors", label: "MCP Connectors", icon: "connectors" },
  ] },
  { title: "Govern", items: [
    { to: "/audit", label: "Audit Log", icon: "audit" },
    { to: "/settings", label: "Settings", icon: "settings" },
  ] },
];
const ALL = GROUPS.flatMap((g) => g.items);

function Brand() {
  return (
    <div className="flex items-center gap-2.5 px-2">
      <div className="brand-gradient grid h-9 w-9 place-items-center rounded-xl text-sm font-bold text-white shadow-lg shadow-brand-600/30">AF</div>
      <div>
        <div className="text-sm font-semibold text-white">Agent Factory</div>
        <div className="text-[11px] text-slate-400">Design · Build · Test · Govern</div>
      </div>
    </div>
  );
}

function Nav({ pending, onNavigate }: { pending: number; onNavigate?: () => void }) {
  return (
    <nav className="flex flex-1 flex-col gap-5 overflow-y-auto pr-1" aria-label="Main">
      {GROUPS.map((group) => (
        <div key={group.title}>
          <div className="mb-1.5 px-2.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-500">{group.title}</div>
          <div className="flex flex-col gap-0.5">
            {group.items.map((item) => {
              const Icon = icons[item.icon];
              return (
                <NavLink key={item.to} to={item.to} end={item.end} onClick={onNavigate}
                  className={({ isActive }) => cx("group relative flex items-center gap-2.5 rounded-lg px-2.5 py-1.5 text-sm transition",
                    isActive ? "bg-white/10 font-medium text-white" : "text-slate-400 hover:bg-white/5 hover:text-slate-100")}>
                  {({ isActive }) => (
                    <>
                      {isActive && <span className="brand-gradient absolute top-1.5 bottom-1.5 -left-3 w-1 rounded-r-full" />}
                      <Icon className={cx("h-4 w-4", isActive ? "text-brand-400" : "text-slate-500 group-hover:text-slate-300")} />
                      <span className="flex-1">{item.label}</span>
                      {item.badge && pending > 0 && (
                        <span className="rounded-full bg-amber-400 px-1.5 text-[11px] font-semibold text-slate-950">{pending}</span>
                      )}
                    </>
                  )}
                </NavLink>
              );
            })}
          </div>
        </div>
      ))}
    </nav>
  );
}

function ThemeSwitch() {
  const [pref, setPref] = useState<ThemePreference>(getThemePreference());
  const options: { id: ThemePreference; icon: IconName; label: string }[] = [
    { id: "light", icon: "sun", label: "Light" }, { id: "system", icon: "monitor", label: "System" }, { id: "dark", icon: "moon", label: "Dark" },
  ];
  return (
    <div className="flex rounded-lg border border-slate-200 bg-white p-0.5 dark:border-white/10 dark:bg-white/5" role="radiogroup" aria-label="Theme">
      {options.map(({ id, icon, label }) => {
        const Icon = icons[icon];
        return (
          <button key={id} role="radio" aria-checked={pref === id} title={label} aria-label={label}
            onClick={() => { setPref(id); setThemePreference(id); }}
            className={cx("grid h-7 w-7 place-items-center rounded-md transition",
              pref === id ? "bg-slate-900 text-white dark:bg-white dark:text-slate-900" : "text-slate-500 hover:text-slate-900 dark:hover:text-white")}>
            <Icon className="h-3.5 w-3.5" />
          </button>
        );
      })}
    </div>
  );
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const [index, setIndex] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const { data: agents } = useApi<any>(open ? "/api/v1/agents?limit=200" : null);
  const results = useMemo((): { id: string; label: string; hint: string; icon: IconName; to: string }[] => {
    const q = query.trim().toLowerCase();
    const pages = ALL.map((i) => ({ id: i.to, label: i.label, hint: "Page", icon: i.icon, to: i.to }));
    const agentRows = (agents?.items ?? []).map((a: any) => ({
      id: a.id, label: a.display_name || a.name, hint: `Agent · ${a.name}`, icon: "agents" as IconName, to: `/agents/${a.id}`,
    }));
    return [...pages, ...agentRows].filter((r) => !q || `${r.label} ${r.hint}`.toLowerCase().includes(q)).slice(0, 12);
  }, [agents, query]);
  useEffect(() => { if (open) { setQuery(""); setIndex(0); setTimeout(() => input.current?.focus(), 0); } }, [open]);
  if (!open) return null;
  const go = (to: string) => { onClose(); navigate(to); };
  return (
    <div className="animate-fade fixed inset-0 z-50 flex items-start justify-center bg-slate-950/50 px-4 pt-[12vh] backdrop-blur-sm" onClick={onClose}>
      <div role="dialog" aria-modal="true" aria-label="Go to" onClick={(e) => e.stopPropagation()}
        className="animate-rise w-full max-w-lg overflow-hidden rounded-2xl border border-slate-200 bg-white shadow-2xl dark:border-white/10 dark:bg-slate-900">
        <div className="flex items-center gap-2 border-b border-slate-100 px-4 dark:border-white/10">
          <icons.search className="h-4 w-4 text-slate-400" />
          <input ref={input} value={query} placeholder="Jump to a page or agent…" aria-label="Search pages and agents"
            onChange={(e) => { setQuery(e.target.value); setIndex(0); }}
            onKeyDown={(e) => {
              if (e.key === "Escape") onClose();
              if (e.key === "ArrowDown") { e.preventDefault(); setIndex((i) => Math.min(results.length - 1, i + 1)); }
              if (e.key === "ArrowUp") { e.preventDefault(); setIndex((i) => Math.max(0, i - 1)); }
              if (e.key === "Enter" && results[index]) go(results[index].to);
            }}
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-slate-400" />
          <kbd className="rounded border border-slate-200 px-1.5 text-[10px] text-slate-400 dark:border-white/10">esc</kbd>
        </div>
        <ul className="max-h-80 overflow-y-auto p-2" role="listbox">
          {results.length === 0 && <li className="px-3 py-6 text-center text-sm text-slate-500">No matches</li>}
          {results.map((r, i) => {
            const Icon = icons[r.icon];
            return (
              <li key={r.id} role="option" aria-selected={i === index}>
                <button onMouseEnter={() => setIndex(i)} onClick={() => go(r.to)}
                  className={cx("flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm",
                    i === index ? "bg-brand-50 text-brand-700 dark:bg-brand-500/15 dark:text-brand-300" : "text-slate-700 dark:text-slate-200")}>
                  <Icon className="h-4 w-4 opacity-70" /><span className="flex-1 truncate">{r.label}</span>
                  <span className="text-xs text-slate-400">{r.hint}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

export default function Layout() {
  const { me, authError } = useSession();
  const location = useLocation();
  const { data: pending } = useApi<{ total: number }>(authError ? null : "/api/v1/approvals?status=pending&limit=1", { interval: 5000 });
  const [palette, setPalette] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const current = ALL.find((i) => (i.end ? location.pathname === i.to : location.pathname.startsWith(i.to))) ?? ALL[0];
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); setPalette((v) => !v); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  const initials = (me?.display_name ?? "?").split(" ").map((w) => w[0]).join("").slice(0, 2).toUpperCase();
  const sidebar = (onNavigate?: () => void) => (
    <div className="flex h-full flex-col gap-6 bg-slate-950 px-3 py-5 [background-image:radial-gradient(400px_200px_at_0%_0%,rgb(99_102_241/0.22),transparent_70%)]">
      <Brand />
      <Nav pending={pending?.total ?? 0} onNavigate={onNavigate} />
      <div className="rounded-xl border border-white/10 bg-white/5 p-3 text-xs text-slate-400">
        {me ? (
          <div className="flex items-center gap-2.5">
            <div className="grid h-8 w-8 place-items-center rounded-full bg-gradient-to-br from-slate-700 to-slate-800 text-[11px] font-semibold text-white">{initials}</div>
            <div className="min-w-0">
              <div className="truncate font-medium text-slate-100">{me.display_name}</div>
              <div className="truncate">{me.role} · {me.workspace?.name} · {me.auth_method}</div>
            </div>
          </div>
        ) : authError ? "Not signed in — add an API key in Settings" : "…"}
      </div>
    </div>
  );
  return (
    <div className="flex min-h-screen">
      <aside className="sticky top-0 hidden h-screen w-64 shrink-0 md:block">{sidebar()}</aside>
      {drawer && (
        <div className="animate-fade fixed inset-0 z-40 bg-slate-950/60 md:hidden" onClick={() => setDrawer(false)}>
          <aside className="h-full w-72" onClick={(e) => e.stopPropagation()}>{sidebar(() => setDrawer(false))}</aside>
        </div>
      )}
      <div className="app-backdrop flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex items-center gap-3 border-b border-slate-200/70 bg-white/70 px-4 py-2.5 backdrop-blur-xl sm:px-6 dark:border-white/[0.06] dark:bg-slate-950/60">
          <button className="rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 md:hidden dark:hover:bg-white/5" aria-label="Open navigation" onClick={() => setDrawer(true)}>
            <icons.menu className="h-5 w-5" />
          </button>
          <div className="flex min-w-0 items-center gap-2 text-sm">
            <span className="hidden text-slate-400 sm:inline">{GROUPS.find((g) => g.items.includes(current))?.title}</span>
            <span className="hidden text-slate-300 sm:inline dark:text-slate-600">/</span>
            <span className="truncate font-medium text-slate-800 dark:text-slate-100">{current.label}</span>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button onClick={() => setPalette(true)}
              className="hidden items-center gap-2 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-400 shadow-sm hover:text-slate-600 sm:flex dark:border-white/10 dark:bg-white/5">
              <icons.search className="h-4 w-4" /> <span className="pr-6">Search</span>
              <kbd className="rounded border border-slate-200 px-1 text-[10px] dark:border-white/10">⌘K</kbd>
            </button>
            <ThemeSwitch />
          </div>
        </header>
        <main key={location.pathname} className="animate-rise mx-auto w-full max-w-7xl flex-1 px-4 py-7 sm:px-6">
          <Outlet />
        </main>
      </div>
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
    </div>
  );
}
