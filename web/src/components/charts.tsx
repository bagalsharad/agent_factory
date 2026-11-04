import { useEffect, useState } from "react";
import { icons } from "./icons";
import { cx } from "./ui";

export interface ActivityBucket {
  start: string;
  completed: number;
  failed: number;
  other: number;
  tokens: number;
  p95_latency_ms: number | null;
}

/** Measures the element as soon as it mounts (and again whenever it is re-mounted or resized). */
function useWidth<T extends HTMLElement>(fallback = 640) {
  const [el, setEl] = useState<T | null>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    if (!el) return;
    const measure = (w: number) => w > 0 && setWidth(Math.max(240, Math.floor(w)));
    measure(el.getBoundingClientRect().width);
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => measure(entry.contentRect.width));
    observer.observe(el);
    return () => observer.disconnect();
  }, [el]);
  return [setEl, width] as const;
}

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * magnitude).find((s) => value / s <= 4) ?? magnitude * 10;
  return Math.ceil(value / step) * step;
}

function label(iso: string, hours: number): string {
  const d = new Date(iso);
  return hours > 48
    ? d.toLocaleDateString(undefined, { month: "short", day: "numeric" })
    : d.toLocaleTimeString(undefined, { hour: "numeric" });
}

/** A column whose top (data end) has 4px rounded corners and whose baseline end is square. */
function columnPath(x: number, y: number, w: number, h: number, roundTop: boolean): string {
  const r = roundTop ? Math.min(4, h, w / 2) : 0;
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`;
}

const SERIES = [
  { key: "completed" as const, name: "Completed", color: "var(--series-1)", Icon: icons.check },
  { key: "failed" as const, name: "Failed", color: "var(--status-critical)", Icon: icons.x },
];

/** Executions per time bucket, stacked by outcome (completed at the baseline, failed above it). */
export function ActivityChart({ items, hours, height = 200 }: { items: ActivityBucket[]; hours: number; height?: number }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const [asTable, setAsTable] = useState(false);
  const pad = { top: 12, right: 8, bottom: 24, left: 32 };
  const innerW = width - pad.left - pad.right;
  const innerH = height - pad.top - pad.bottom;
  const max = niceMax(Math.max(0, ...items.map((b) => b.completed + b.failed)));
  const band = innerW / Math.max(1, items.length);
  const barW = Math.min(24, Math.max(3, band * 0.62));
  const yScale = (v: number) => (v / max) * innerH;
  const ticks = [0, max / 2, max];
  const every = Math.ceil(items.length / Math.max(2, Math.floor(innerW / 72)));
  const total = items.reduce((n, b) => n + b.completed + b.failed + b.other, 0);
  const active = hover != null ? items[hover] : null;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3 text-xs text-slate-600 dark:text-slate-300">
        <ul className="flex flex-wrap items-center gap-4" aria-label="Legend">
          {SERIES.map(({ key, name, color, Icon }) => (
            <li key={key} className="flex items-center gap-1.5">
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: color }} />
              <Icon className="h-3.5 w-3.5 text-slate-400" /> {name}
            </li>
          ))}
        </ul>
        <button onClick={() => setAsTable(!asTable)} className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-slate-500 hover:bg-slate-100 dark:hover:bg-white/5"
          aria-pressed={asTable}>
          {asTable ? <><icons.chart className="h-3.5 w-3.5" /> Chart</> : <><icons.table className="h-3.5 w-3.5" /> Table</>}
        </button>
      </div>
      {asTable ? (
        <div className="max-h-64 overflow-auto">
          <table className="w-full text-left text-xs tabular-nums">
            <thead className="text-slate-400"><tr><th className="py-1">Period starting</th><th>Completed</th><th>Failed</th><th>Other</th><th>p95</th><th>Tokens</th></tr></thead>
            <tbody className="divide-y divide-slate-100 dark:divide-white/5">
              {items.map((b) => (
                <tr key={b.start}><td className="py-1">{new Date(b.start).toLocaleString()}</td><td>{b.completed}</td><td>{b.failed}</td><td>{b.other}</td>
                  <td>{b.p95_latency_ms == null ? "—" : `${Math.round(b.p95_latency_ms)} ms`}</td><td>{b.tokens.toLocaleString()}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div ref={ref} className="relative w-full overflow-hidden" onMouseLeave={() => setHover(null)}>
          <svg width={width} height={height} role="img" className="block"
            aria-label={`Executions over the last ${hours} hours: ${total} in total. Switch to the table view for every value.`}>
            <g transform={`translate(${pad.left},${pad.top})`}>
              {ticks.map((t) => (
                <g key={t} transform={`translate(0,${innerH - yScale(t)})`}>
                  <line x1={0} x2={innerW} stroke={t === 0 ? "var(--chart-axis)" : "var(--chart-grid)"} strokeWidth={1} />
                  <text x={-8} dy="0.32em" textAnchor="end" fontSize={11} fill="var(--chart-muted)" className="tabular-nums">{t}</text>
                </g>
              ))}
              {items.map((b, i) => {
                const x = i * band + (band - barW) / 2;
                const hc = yScale(b.completed);
                const hf = yScale(b.failed);
                const gap = b.completed && b.failed ? 2 : 0; // surface gap between stacked segments
                return (
                  <g key={b.start} className="chart-mark">
                    {hover === i && <rect x={i * band} y={0} width={band} height={innerH} fill="var(--chart-grid)" opacity={0.45} rx={4} />}
                    {b.completed > 0 && <path d={columnPath(x, innerH - hc, barW, hc, b.failed === 0)} fill="var(--series-1)" />}
                    {b.failed > 0 && <path d={columnPath(x, innerH - hc - gap - hf, barW, hf, true)} fill="var(--status-critical)" />}
                    {i % every === 0 && (i === 0 || label(b.start, hours) !== label(items[i - every]?.start ?? "", hours)) && (
                      <text x={i * band + band / 2} y={innerH + 16} textAnchor="middle" fontSize={11} fill="var(--chart-muted)">{label(b.start, hours)}</text>
                    )}
                    <rect x={i * band} y={0} width={band} height={innerH + pad.bottom} fill="transparent"
                      onMouseEnter={() => setHover(i)} onFocus={() => setHover(i)} tabIndex={-1} />
                  </g>
                );
              })}
            </g>
          </svg>
          {total === 0 && (
            <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
              <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white/90 px-3 py-1.5 text-xs text-slate-500 shadow-sm dark:border-white/10 dark:bg-slate-900/90">
                <icons.clock className="h-3.5 w-3.5" /> No production runs in this window
              </div>
            </div>
          )}
          {active && hover != null && total > 0 && (
            <div role="tooltip"
              className={cx("pointer-events-none absolute top-1 z-10 min-w-40 rounded-xl border border-slate-200 bg-white/95 px-3 py-2 text-xs shadow-lg backdrop-blur",
                "dark:border-white/10 dark:bg-slate-900/95")}
              style={{ left: Math.min(width - 176, Math.max(0, pad.left + hover * band + band / 2 - 80)) }}>
              <div className="mb-1 font-medium text-slate-900 dark:text-white">{new Date(active.start).toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}</div>
              {SERIES.map(({ key, name, color }) => (
                <div key={key} className="flex items-center justify-between gap-4 text-slate-600 dark:text-slate-300">
                  <span className="flex items-center gap-1.5"><span className="h-2 w-2 rounded-sm" style={{ background: color }} />{name}</span>
                  <span className="tabular-nums text-slate-900 dark:text-white">{active[key]}</span>
                </div>
              ))}
              {active.other > 0 && <div className="flex justify-between gap-4 text-slate-500"><span>Other (running, cancelled)</span><span className="tabular-nums">{active.other}</span></div>}
              <div className="mt-1 flex justify-between gap-4 border-t border-slate-100 pt-1 text-slate-500 dark:border-white/10">
                <span>p95 latency</span><span className="tabular-nums">{active.p95_latency_ms == null ? "—" : `${Math.round(active.p95_latency_ms)} ms`}</span>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
