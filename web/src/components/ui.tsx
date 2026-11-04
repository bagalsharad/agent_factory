import { useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { ApiError } from "../lib/api";
import { icons, type IconName } from "./icons";

// eslint-disable-next-line react-refresh/only-export-components
export function cx(...classes: (string | false | null | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

export function Card({ title, actions, children, className, icon, subtitle, flush }: {
  title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; icon?: IconName;
  subtitle?: ReactNode; flush?: boolean;
}) {
  const Icon = icon ? icons[icon] : null;
  return (
    <section className={cx(
      "min-w-0 rounded-2xl border border-slate-200/80 bg-white/90 shadow-[0_1px_2px_rgb(15_23_42/0.04),0_8px_24px_-12px_rgb(15_23_42/0.08)] backdrop-blur",
      "dark:border-white/[0.07] dark:bg-slate-900/70 dark:shadow-none", className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-3 px-5 pt-4 pb-3">
          <div className="flex min-w-0 items-center gap-2.5">
            {Icon && (
              <span className="grid h-7 w-7 shrink-0 place-items-center rounded-lg bg-brand-50 text-brand-600 dark:bg-brand-500/15 dark:text-brand-400">
                <Icon className="h-4 w-4" />
              </span>
            )}
            <div className="min-w-0">
              <h2 className="truncate text-sm font-semibold text-slate-800 dark:text-slate-100">{title}</h2>
              {subtitle && <p className="truncate text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">{actions}</div>
        </header>
      )}
      <div className={flush ? "" : cx("px-5 pb-5", !(title || actions) && "pt-5")}>{children}</div>
    </section>
  );
}

type Tone = "good" | "warn" | "bad" | "info" | "neutral" | "brand";

const TONES: Record<Tone, string> = {
  good: "bg-emerald-50 text-emerald-700 ring-emerald-600/20 dark:bg-emerald-500/10 dark:text-emerald-300 dark:ring-emerald-400/20",
  warn: "bg-amber-50 text-amber-800 ring-amber-600/25 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-400/20",
  bad: "bg-rose-50 text-rose-700 ring-rose-600/20 dark:bg-rose-500/10 dark:text-rose-300 dark:ring-rose-400/20",
  info: "bg-sky-50 text-sky-700 ring-sky-600/20 dark:bg-sky-500/10 dark:text-sky-300 dark:ring-sky-400/20",
  neutral: "bg-slate-100 text-slate-600 ring-slate-500/20 dark:bg-white/5 dark:text-slate-300 dark:ring-white/10",
  brand: "bg-brand-50 text-brand-700 ring-brand-600/20 dark:bg-brand-500/10 dark:text-brand-300 dark:ring-brand-400/20",
};
const DOTS: Record<Tone, string> = {
  good: "bg-emerald-500", warn: "bg-amber-500", bad: "bg-rose-500", info: "bg-sky-500", neutral: "bg-slate-400", brand: "bg-brand-500",
};

const STATUS_TONE: Record<string, Tone> = {
  completed: "good", published: "good", up: "good", ok: "good", passed: "good", allow: "good", active: "good", healthy: "good",
  approved: "brand", reviewed: "brand",
  running: "info", testing: "info", validating: "info", queued: "neutral", draft: "neutral",
  waiting_approval: "warn", pending: "warn", awaiting_approval: "warn", require_approval: "warn", pending_review: "warn",
  pending_approval: "warn", degraded: "warn", warning: "warn", medium: "warn", in_doubt: "warn",
  deprecated: "warn", superseded: "neutral", disabled: "neutral", cancelled: "neutral", expired: "neutral", retired: "neutral",
  low: "neutral", unknown: "neutral", reset: "neutral",
  failed: "bad", rejected: "bad", denied: "bad", deny: "bad", budget_exceeded: "bad", down: "bad", fail: "bad", error: "bad",
  critical: "bad", high: "bad", rolled_back: "bad", dead_lettered: "bad", unhealthy: "bad", exhausted: "bad",
};

export function Badge({ value, className, title }: { value?: string | null; className?: string; title?: string }) {
  if (!value) return <span className="text-slate-400">—</span>;
  const tone = STATUS_TONE[value] ?? "brand";
  const pulse = value === "running" || value === "waiting_approval";
  return (
    <span title={title} className={cx("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset", TONES[tone], className)}>
      <span className="relative flex h-1.5 w-1.5">
        {pulse && <span className={cx("absolute inline-flex h-full w-full animate-ping rounded-full opacity-60", DOTS[tone])} />}
        <span className={cx("relative inline-flex h-1.5 w-1.5 rounded-full", DOTS[tone])} />
      </span>
      {value.replaceAll("_", " ")}
    </span>
  );
}

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" | "ghost"; busy?: boolean; icon?: IconName };

export function Button({ variant = "secondary", busy, className, children, disabled, icon, ...rest }: ButtonProps) {
  const styles = {
    primary: "brand-gradient text-white shadow-sm shadow-brand-600/30 hover:brightness-110 disabled:opacity-50 disabled:shadow-none",
    secondary: "border border-slate-200 bg-white text-slate-700 shadow-sm hover:border-slate-300 hover:bg-slate-50 disabled:opacity-50 dark:border-white/10 dark:bg-white/5 dark:text-slate-200 dark:hover:bg-white/10",
    danger: "bg-rose-600 text-white shadow-sm hover:bg-rose-500 disabled:opacity-50",
    ghost: "text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-white/10",
  }[variant];
  const Icon = icon ? icons[icon] : null;
  return (
    <button {...rest} disabled={disabled || busy}
      className={cx("inline-flex items-center justify-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-500 disabled:cursor-not-allowed", styles, className)}>
      {busy ? <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" /> : Icon && <Icon className="h-4 w-4" />}
      {children}
    </button>
  );
}

export function Stat({ label, value, hint, tone, icon, trend }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: "good" | "bad" | "warn"; icon?: IconName;
  trend?: number[];
}) {
  const toneClass = tone === "bad" ? "text-rose-600 dark:text-rose-400" : tone === "warn" ? "text-amber-600 dark:text-amber-400"
    : tone === "good" ? "text-emerald-600 dark:text-emerald-400" : "text-slate-900 dark:text-white";
  const Icon = icon ? icons[icon] : null;
  return (
    <div className="relative min-w-0 overflow-hidden rounded-2xl border border-slate-200/80 bg-white/90 p-4 shadow-[0_1px_2px_rgb(15_23_42/0.04)] dark:border-white/[0.07] dark:bg-slate-900/70">
      <div className="flex items-start justify-between gap-2">
        <div className="text-xs font-medium text-slate-500 dark:text-slate-400">{label}</div>
        {Icon && <Icon className="h-4 w-4 text-slate-400 dark:text-slate-500" />}
      </div>
      <div className="mt-1.5 flex items-end justify-between gap-3">
        <div className={cx("text-2xl font-semibold tracking-tight", toneClass)}>{value}</div>
        {trend && trend.length > 1 && trend.some((v) => v > 0) && <Sparkline values={trend} />}
      </div>
      {hint && <div className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</div>}
    </div>
  );
}

/** 12-point style trend: de-emphasized line, the current (last) point in the accent. */
export function Sparkline({ values, width = 84, height = 28 }: { values: number[]; width?: number; height?: number }) {
  const max = Math.max(...values, 1);
  const step = width / Math.max(1, values.length - 1);
  const y = (v: number) => height - 3 - (v / max) * (height - 6);
  const points = values.map((v, i) => `${(i * step).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const last = values[values.length - 1];
  return (
    <svg width={width} height={height} className="shrink-0 overflow-visible" role="img"
      aria-label={`Trend: ${values.join(", ")}`}>
      <polyline points={points} fill="none" stroke="var(--chart-muted)" strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" opacity={0.7} />
      <circle cx={width} cy={y(last)} r={4} fill="var(--series-1)" stroke="var(--chart-surface)" strokeWidth={2} />
    </svg>
  );
}

export function Table({ head, children, empty }: { head: ReactNode[]; children: ReactNode; empty?: boolean }) {
  return (
    <div className="-mx-1 overflow-x-auto">
      <table className="w-full text-left text-sm">
        <thead>
          <tr className="border-b border-slate-200/80 dark:border-white/[0.07]">
            {head.map((h, i) => <th key={i} className="px-3 py-2 text-[11px] font-semibold uppercase tracking-wider text-slate-400 dark:text-slate-500">{h}</th>)}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-100 tabular-nums dark:divide-white/[0.05] [&>tr]:transition-colors [&>tr:hover]:bg-slate-50/80 dark:[&>tr:hover]:bg-white/[0.03]">{children}</tbody>
      </table>
      {empty && <Empty>Nothing here yet.</Empty>}
    </div>
  );
}

export function Td({ children, className, colSpan, title }: { children?: ReactNode; className?: string; colSpan?: number; title?: string }) {
  return <td colSpan={colSpan} title={title} className={cx("px-3 py-2.5 align-top", className)}>{children}</td>;
}

export function Tabs({ tabs, active, onChange }: { tabs: { id: string; label: ReactNode }[]; active: string; onChange: (id: string) => void }) {
  return (
    <div className="flex gap-1 overflow-x-auto rounded-xl border border-slate-200/80 bg-white/70 p-1 dark:border-white/[0.07] dark:bg-white/[0.03]" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={active === t.id} onClick={() => onChange(t.id)}
          className={cx("whitespace-nowrap rounded-lg px-3 py-1.5 text-sm font-medium transition",
            active === t.id ? "bg-slate-900 text-white shadow-sm dark:bg-white dark:text-slate-900"
              : "text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-white/5 dark:hover:text-slate-200")}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

export function JsonView({ value, maxHeight = "28rem" }: { value: unknown; maxHeight?: string }) {
  return (
    <pre style={{ maxHeight }} className="overflow-auto rounded-xl border border-white/5 bg-slate-950 p-3.5 font-mono text-xs leading-relaxed text-slate-200 shadow-inner">
      {typeof value === "string" ? value : JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 py-8 text-center text-sm text-slate-500 dark:text-slate-400">
      <span className="grid h-9 w-9 place-items-center rounded-full bg-slate-100 text-slate-400 dark:bg-white/5"><icons.layers className="h-4 w-4" /></span>
      {children}
    </div>
  );
}

export function ErrorBox({ error }: { error?: ApiError | null }) {
  if (!error) return null;
  return (
    <div role="alert" className="flex gap-3 rounded-xl border border-rose-200 bg-rose-50/80 p-3 text-sm text-rose-800 dark:border-rose-500/30 dark:bg-rose-500/10 dark:text-rose-200">
      <icons.alert className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0">
        <div className="font-medium">{error.message}</div>
        <div className="mt-0.5 text-xs opacity-80">{error.code}{error.requestId ? ` · request ${error.requestId}` : ""}</div>
        {Array.isArray(error.details) && (
          <ul className="mt-2 list-disc pl-5 text-xs">
            {(error.details as any[]).slice(0, 12).map((d, i) => <li key={i}>{d.path ? <code>{d.path}</code> : null} {d.message ?? JSON.stringify(d)}</li>)}
          </ul>
        )}
      </div>
    </div>
  );
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-slate-500" role="status">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-brand-500 border-t-transparent" /> {label}
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

export const inputClass = "w-full rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-sm shadow-sm transition placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-4 focus:ring-brand-500/15 dark:border-white/10 dark:bg-slate-950/60";

export function PageHeader({ title, subtitle, actions, eyebrow }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; eyebrow?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-brand-600 dark:text-brand-400">{eyebrow}</div>}
        <h1 className="text-2xl font-semibold tracking-tight text-slate-900 dark:text-white">{title}</h1>
        {subtitle && <p className="mt-1 max-w-3xl text-sm text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </div>
      <div className="flex flex-wrap gap-2">{actions}</div>
    </div>
  );
}

export function Collapsible({ title, children, defaultOpen = false }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="rounded-xl border border-slate-200/80 dark:border-white/[0.07]">
      <button onClick={() => setOpen(!open)} aria-expanded={open} className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium">
        {title}<span className={cx("text-slate-400 transition-transform", open && "rotate-90")}>›</span>
      </button>
      {open && <div className="animate-fade border-t border-slate-200/80 p-3 dark:border-white/[0.07]">{children}</div>}
    </div>
  );
}
