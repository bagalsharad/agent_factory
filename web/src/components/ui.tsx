import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ApiError } from "../lib/api";

// eslint-disable-next-line react-refresh/only-export-components
export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function Card({ title, actions, children, className }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string;
}) {
  return (
    <section className={cx("rounded-xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900", className)}>
      {(title || actions) && (
        <header className="flex items-center justify-between gap-3 border-b border-slate-100 px-4 py-3 dark:border-slate-800">
          <h2 className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</h2>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

const STATUS_COLORS: Record<string, string> = {
  completed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  published: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  approved: "bg-teal-100 text-teal-800 dark:bg-teal-950 dark:text-teal-300",
  up: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  ok: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  passed: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  allow: "bg-emerald-100 text-emerald-800 dark:bg-emerald-950 dark:text-emerald-300",
  running: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  queued: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  testing: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  validating: "bg-sky-100 text-sky-800 dark:bg-sky-950 dark:text-sky-300",
  draft: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  waiting_approval: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  pending: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  awaiting_approval: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  require_approval: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  pending_review: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  deprecated: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  disabled: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  cancelled: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  expired: "bg-zinc-200 text-zinc-700 dark:bg-zinc-800 dark:text-zinc-300",
  failed: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  rejected: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  denied: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  deny: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  budget_exceeded: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  down: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  fail: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  error: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
  low: "bg-slate-200 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  medium: "bg-amber-100 text-amber-800 dark:bg-amber-950 dark:text-amber-300",
  high: "bg-orange-100 text-orange-800 dark:bg-orange-950 dark:text-orange-300",
  critical: "bg-rose-100 text-rose-800 dark:bg-rose-950 dark:text-rose-300",
};

export function Badge({ value, className, title }: { value?: string | null; className?: string; title?: string }) {
  if (!value) return <span className="text-slate-400">—</span>;
  return (
    <span title={title} className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap",
      STATUS_COLORS[value] ?? "bg-indigo-100 text-indigo-800 dark:bg-indigo-950 dark:text-indigo-300", className)}>
      {value.replaceAll("_", " ")}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; busy?: boolean };

export function Button({ variant = "secondary", busy, className, children, disabled, ...rest }: ButtonProps) {
  const styles = {
    primary: "bg-indigo-600 text-white hover:bg-indigo-500 disabled:bg-indigo-300 dark:disabled:bg-indigo-900",
    secondary: "border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200 dark:hover:bg-slate-800",
    danger: "bg-rose-600 text-white hover:bg-rose-500 disabled:bg-rose-300",
    ghost: "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-800",
  }[variant];
  return (
    <button {...rest} disabled={disabled || busy}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition disabled:cursor-not-allowed", styles, className)}>
      {busy && <span className="h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </button>
  );
}

export function Stat({ label, value, hint, tone }: { label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "bad" | "warn" }) {
  const toneClass = tone === "bad" ? "text-rose-600 dark:text-rose-400" : tone === "warn" ? "text-amber-600 dark:text-amber-400"
    : tone === "good" ? "text-emerald-600 dark:text-emerald-400" : "text-slate-900 dark:text-white";
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <div className="text-xs font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</div>
      <div className={cx("mt-1 text-2xl font-semibold tabular-nums", toneClass)}>{value}</div>
      {hint && <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</div>}
    </div>
  );
}

export function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">
          <tr className="border-b border-slate-200 dark:border-slate-800">
            {head.map((h, i) => <th key={i} className="px-3 py-2 font-medium">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 dark:divide-slate-800">{children}</tbody>
      </table>
      {empty && <Empty>Nothing here yet.</Empty>}
    </div>
  );
}

export function Td({ children, className, colSpan, title }: { children?: ReactNode; className?: string; colSpan?: number; title?: string }) {
  return <td colSpan={colSpan} title={title} className={cx("px-3 py-2 align-top", className)}>{children}</td>;
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: ReactNode }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto border-b border-slate-200 dark:border-slate-800" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} onClick={() => onChange(t.id)}
          className={cx("whitespace-nowrap border-b-2 px-3 py-2 text-sm font-medium",
            active === t.id ? "border-indigo-600 text-indigo-700 dark:text-indigo-300" : "border-transparent text-slate-500 hover:text-slate-800 dark:hover:text-slate-200")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function JsonView({ value, maxHeight = "28rem" }: { value: unknown; maxHeight?: string }) {
  return (
    <pre style={{ maxHeight }} className="overflow-auto rounded-lg bg-slate-950 p-3 text-xs leading-relaxed text-slate-100">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="py-8 text-center text-sm text-slate-500 dark:text-slate-400">{children}</div>;
}

export function ErrorBox({ error }: { error?: ApiError | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-rose-200 bg-rose-50 p-3 text-sm text-rose-800 dark:border-rose-900 dark:bg-rose-950 dark:text-rose-200">
      <div className="font-medium">{error.message}</div>
      <div className="mt-0.5 text-xs opacity-80">{error.code}{error.requestId ? ` · request ${error.requestId}` : ""}</div>
      {Array.isArray(error.details) && (
        <ul className="mt-2 list-disc pl-5 text-xs">
          {(error.details as any[]).slice(0, 12).map((d, i) => <li key={i}>{d.path ? <code>{d.path}</code> : null} {d.message ?? JSON.stringify(d)}</li>)}
        </ul>
      )}
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-indigo-500 border-t-transparent" /> {label}
    </div>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-slate-600 dark:text-slate-300">{label}</span>
      {children}
      {hint && <span className="block text-xs text-slate-500 dark:text-slate-400">{hint}</span>}
    </label>
  );
}

export const inputClass = "w-full rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm shadow-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500 dark:border-slate-700 dark:bg-slate-950";

export function PageHeader({ title, subtitle, actions }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

export function Collapsible({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-lg border border-slate-200 dark:border-slate-800">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium">
        {title}<span className="text-slate-400">{open ? "−" : "+"}</span>
      </button>
      {open && <div className="border-t border-slate-200 p-3 dark:border-slate-800">{children}</div>}
    </div>
  );
}
