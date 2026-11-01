import { cx } from "./ui";

export interface GraphNode {
  id: string;
  kind: string;
  label: string;
  tool?: string | null;
  agent?: string | null;
  action?: string | null;
  final?: boolean;
  tools?: string[];
  description?: string;
}

export interface Graph {
  pattern?: string;
  nodes: GraphNode[];
  edges: { from: string; to: string; label?: string; style?: string }[];
}

export type NodeStatus = "pending" | "running" | "completed" | "failed" | "waiting_approval" | "rejected";

const W = 190;
const H = 46;
const GAP_Y = 78;
const COL = [40, 300, 560];

const STATUS_STYLE: Record<NodeStatus, { fill: string; stroke: string }> = {
  pending: { fill: "fill-white dark:fill-slate-900", stroke: "stroke-slate-300 dark:stroke-slate-700" },
  running: { fill: "fill-sky-50 dark:fill-sky-950", stroke: "stroke-sky-500" },
  completed: { fill: "fill-emerald-50 dark:fill-emerald-950", stroke: "stroke-emerald-500" },
  failed: { fill: "fill-rose-50 dark:fill-rose-950", stroke: "stroke-rose-500" },
  waiting_approval: { fill: "fill-amber-50 dark:fill-amber-950", stroke: "stroke-amber-500" },
  rejected: { fill: "fill-rose-50 dark:fill-rose-950", stroke: "stroke-rose-400" },
};

const KIND_ICON: Record<string, string> = {
  start: "▶", end: "■", finalize: "✓", model: "◆", agent_loop: "⟳", tools: "⚙", tool: "⚙", validate: "☑",
  approval: "✋", delegate: "→", retrieve: "⌕", specialist: "◎",
};

export default function WorkflowGraph({ graph, status = {}, selected, onSelect }: {
  graph: Graph; status?: Record<string, NodeStatus>; selected?: string | null; onSelect?: (id: string) => void;
}) {
  const positions: Record<string, { x: number; y: number }> = {};
  let row = 0;
  const main = graph.nodes.filter((n) => n.kind !== "tools" && n.kind !== "specialist");
  for (const node of main) {
    positions[node.id] = { x: COL[0], y: 20 + row * GAP_Y };
    row += 1;
  }
  for (const node of graph.nodes.filter((n) => n.kind === "tools")) {
    const loop = node.id.replace(/__tools$/, "");
    positions[node.id] = { x: COL[1], y: positions[loop]?.y ?? 20 };
  }
  const specialists = graph.nodes.filter((n) => n.kind === "specialist");
  const toolsNode = graph.nodes.find((n) => n.kind === "tools");
  specialists.forEach((node, i) => {
    const anchor = toolsNode ? positions[toolsNode.id].y : 20;
    positions[node.id] = { x: COL[2], y: Math.max(10, anchor + (i - (specialists.length - 1) / 2) * 58) };
  });
  const maxY = Math.max(...Object.values(positions).map((p) => p.y), 0) + H + 30;
  const width = specialists.length ? COL[2] + W + 30 : toolsNode ? COL[1] + W + 30 : COL[0] + W + 40;

  const edgePath = (from: { x: number; y: number }, to: { x: number; y: number }, offset = 0) => {
    if (Math.abs(from.x - to.x) < 5) return `M ${from.x + W / 2} ${from.y + H} L ${to.x + W / 2} ${to.y}`;
    const y1 = from.y + H / 2 + offset;
    const y2 = to.y + H / 2 + offset;
    return from.x < to.x ? `M ${from.x + W} ${y1} C ${from.x + W + 40} ${y1}, ${to.x - 40} ${y2}, ${to.x} ${y2}`
      : `M ${from.x} ${y1} C ${from.x - 40} ${y1}, ${to.x + W + 40} ${y2}, ${to.x + W} ${y2}`;
  };

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${width} ${maxY}`} style={{ width: "100%", maxWidth: width }} role="img" aria-label="Workflow graph"
        className="h-auto text-slate-700 dark:text-slate-200">
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-slate-400" />
          </marker>
        </defs>
        {graph.edges.map((e, i) => {
          const from = positions[e.from];
          const to = positions[e.to];
          if (!from || !to) return null;
          const back = e.label === "results";
          return (
            <g key={i}>
              <path d={edgePath(from, to, back ? 10 : e.label === "tool calls" ? -10 : 0)} fill="none"
                className="stroke-slate-400" strokeWidth={1.4} strokeDasharray={e.style === "dashed" ? "5 4" : undefined}
                markerEnd="url(#arrow)" />
            </g>
          );
        })}
        {graph.nodes.map((node) => {
          const pos = positions[node.id];
          if (!pos) return null;
          const nodeStatus = status[node.id] ?? (node.kind === "specialist" ? status[`agent:${node.agent}`] : undefined) ?? "pending";
          const st = STATUS_STYLE[nodeStatus];
          const isSelected = selected === node.id;
          const detail = node.tool ?? node.agent ?? node.action
            ?? (node.tools ? (node.tools.length ? `${node.tools.length} tools` : specialists.length ? "delegation" : "no tools") : node.kind);
          return (
            <g key={node.id} transform={`translate(${pos.x}, ${pos.y})`} onClick={() => onSelect?.(node.id)}
              className={cx(onSelect && "cursor-pointer")} data-testid={`node-${node.id}`}>
              <rect width={W} height={H} rx={10} className={cx(st.fill, st.stroke, nodeStatus === "running" && "animate-pulse")}
                strokeWidth={isSelected ? 3 : 1.5} />
              <text x={14} y={19} className="fill-current text-[12px] font-semibold">
                {KIND_ICON[node.kind] ?? "•"} {node.label.length > 22 ? `${node.label.slice(0, 21)}…` : node.label}
              </text>
              <text x={14} y={35} className="fill-slate-500 text-[10.5px]">
                {String(detail).length > 28 ? `${String(detail).slice(0, 27)}…` : detail}{node.final ? " · final" : ""}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/** Derive per-node status from execution events. */
// eslint-disable-next-line react-refresh/only-export-components
export function statusFromEvents(
  events: { type: string; node_id?: string | null; status?: string | null; payload?: Record<string, any> }[],
): Record<string, NodeStatus> {
  const out: Record<string, NodeStatus> = {};
  let sawStart = false;
  let finished = false;
  for (const e of events) {
    if (e.type === "execution.started") sawStart = true;
    if (e.type === "execution.completed") finished = true;
    if (e.type === "delegation.started" && e.payload?.agent) out[`agent:${e.payload.agent}`] = "running";
    if (e.type === "delegation.completed" && e.payload?.agent) out[`agent:${e.payload.agent}`] = e.status === "completed" ? "completed" : "failed";
    if (!e.node_id) continue;
    if (e.type === "step.started") out[e.node_id] = "running";
    else if (e.type === "step.completed") out[e.node_id] = e.status === "rejected" ? "rejected" : e.status === "failed" ? "failed" : "completed";
    else if (e.type === "step.failed") out[e.node_id] = "failed";
    else if (e.type === "step.paused") out[e.node_id] = "waiting_approval";
    else if (e.type === "delegation.started") out[e.node_id] = "running";
  }
  if (sawStart) out.__start__ = "completed";
  if (finished) {
    out.__finalize__ = "completed";
    out.__end__ = "completed";
  }
  return out;
}
