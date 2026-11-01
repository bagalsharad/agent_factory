import { useState } from "react";
import { dateTime, duration } from "../lib/format";
import { Badge, cx, JsonView } from "./ui";

export interface ExecutionEvent {
  seq: number;
  type: string;
  node_id?: string | null;
  status?: string | null;
  payload: Record<string, any>;
  duration_ms?: number | null;
  created_at: string;
}

const TONE: Record<string, string> = {
  "tool.denied": "border-rose-400", "step.failed": "border-rose-400", "execution.failed": "border-rose-500",
  "model.error": "border-rose-400", "budget.exceeded": "border-rose-500", "approval.requested": "border-amber-400",
  "execution.waiting_approval": "border-amber-400", "approval.decided": "border-amber-400", "execution.completed": "border-emerald-500",
  "validation.failed": "border-rose-400", "validation.passed": "border-emerald-400", "tool.call": "border-indigo-400",
  "model.call": "border-sky-400", "delegation.completed": "border-violet-400",
};

function summary(e: ExecutionEvent): string {
  const p = e.payload ?? {};
  switch (e.type) {
    case "tool.call": return `${p.tool}${p.attempts > 1 ? ` (${p.attempts} attempts)` : ""}${p.error ? ` — ${p.error.code}` : ""}`;
    case "tool.denied": return `${p.tool} denied: ${(p.reasons ?? []).join("; ")}`;
    case "model.call": return `${p.provider}/${p.model} · ${p.usage?.input_tokens ?? "?"}→${p.usage?.output_tokens ?? "?"} tokens${p.tool_calls?.length ? ` · ${p.tool_calls.length} tool call(s)` : ""}`;
    case "model.error": return `${p.error?.code}: ${p.error?.message}`;
    case "approval.requested": return `${p.action} (${p.tool ?? "checkpoint"}) — risk ${p.risk}`;
    case "approval.decided": return `decision: ${e.status}`;
    case "budget.exceeded": return `${p.limit} exceeded (${p.used} / ${p.maximum})`;
    case "validation.failed": return (p.errors ?? []).slice(0, 2).join("; ");
    case "delegation.started": return `→ ${p.agent}`;
    case "delegation.completed": return `← ${p.agent}${p.error ? ` (${p.error.code})` : ""}`;
    case "retrieval": return `${p.hits} hit(s) for "${p.query}"`;
    case "context.managed": return `context ${p.total_tokens}/${p.budget_tokens} tokens; dropped ${p.dropped?.length ?? 0}, truncated ${p.truncated?.length ?? 0}`;
    case "execution.failed": return `${p.error?.code}: ${p.error?.message ?? ""}`;
    default: return "";
  }
}

export default function Timeline({ events, onSelectNode }: { events: ExecutionEvent[]; onSelectNode?: (id: string) => void }) {
  const [open, setOpen] = useState<number | null>(null);
  return (
    <ol className="space-y-1.5">
      {events.map((e) => (
        <li key={e.seq} className={cx("rounded-lg border-l-4 bg-slate-50 px-3 py-1.5 dark:bg-slate-800/50", TONE[e.type] ?? "border-slate-300 dark:border-slate-600")}>
          <button className="flex w-full flex-wrap items-center gap-2 text-left text-sm" onClick={() => setOpen(open === e.seq ? null : e.seq)}>
            <span className="w-7 text-xs tabular-nums text-slate-400">{e.seq}</span>
            <span className="font-medium">{e.type}</span>
            {e.node_id && (
              <span role="link" onClick={(ev) => { ev.stopPropagation(); onSelectNode?.(e.node_id!); }}
                className="rounded bg-white px-1.5 text-xs text-indigo-600 hover:underline dark:bg-slate-900">{e.node_id}</span>
            )}
            {e.status && <Badge value={e.status} />}
            <span className="min-w-0 flex-1 truncate text-xs text-slate-500">{summary(e)}</span>
            <span className="text-xs tabular-nums text-slate-400">{e.duration_ms ? duration(e.duration_ms) : ""}</span>
          </button>
          {open === e.seq && (
            <div className="mt-2 space-y-1">
              <div className="text-xs text-slate-500">{dateTime(e.created_at)}</div>
              <JsonView value={e.payload} maxHeight="18rem" />
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
