import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Card, ErrorBox, inputClass, PageHeader, Spinner, Table, Td } from "../components/ui";
import { dateTime, duration, shortId } from "../lib/format";
import { useApi } from "../lib/hooks";

const STATUSES = ["", "queued", "running", "waiting_approval", "completed", "failed", "cancelled", "budget_exceeded", "rejected"];

export default function Executions() {
  const [agentId, setAgentId] = useState("");
  const [status, setStatus] = useState("");
  const [since, setSince] = useState("");
  const [q, setQ] = useState("");
  const [mode, setMode] = useState("standard");
  const { data: agents } = useApi<any>("/api/v1/agents?limit=200");
  const params = new URLSearchParams({ limit: "100" });
  if (agentId) params.set("agent_id", agentId);
  if (status) params.set("status", status);
  if (since) params.set("since", new Date(since).toISOString());
  if (q) params.set("q", q);
  if (mode) params.set("mode", mode);
  const { data, error, loading } = useApi<any>(`/api/v1/executions?${params}`, { interval: 3000 });

  return (
    <div className="space-y-4">
      <PageHeader title="Execution Monitor" subtitle="Active and historical runs. Filter by agent, status, date or execution ID." />
      <div className="flex flex-wrap gap-2">
        <select aria-label="Agent filter" className={`${inputClass} max-w-xs`} value={agentId} onChange={(e) => setAgentId(e.target.value)}>
          <option value="">all agents</option>
          {agents?.items?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        <select aria-label="Status filter" className={`${inputClass} max-w-[12rem]`} value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => <option key={s} value={s}>{s || "any status"}</option>)}
        </select>
        <input aria-label="Since" type="datetime-local" className={`${inputClass} max-w-[14rem]`} value={since} onChange={(e) => setSince(e.target.value)} />
        <select aria-label="Mode filter" className={`${inputClass} max-w-[12rem]`} value={mode} onChange={(e) => setMode(e.target.value)}>
          <option value="standard">standard runs</option>
          <option value="evaluation">evaluation runs</option>
          <option value="">all runs</option>
        </select>
        <input aria-label="Execution ID" className={`${inputClass} max-w-[14rem] font-mono`} placeholder="execution id prefix" value={q} onChange={(e) => setQ(e.target.value.trim())} />
      </div>
      <ErrorBox error={error} />
      <Card>
        {loading && !data ? <Spinner /> : (
          <Table head={["Execution", "Agent", "Version", "Mode", "Status", "Model calls", "Tool calls", "Duration", "Created"]} empty={!data?.items.length}>
            {data?.items.map((e: any) => (
              <tr key={e.id}>
                <Td><Link to={`/executions/${e.id}`} className="font-mono text-indigo-600 hover:underline">{shortId(e.id)}</Link></Td>
                <Td>{e.agent_name}</Td>
                <Td className="font-mono text-xs">{e.version}</Td>
                <Td className="text-xs">{e.mode}</Td>
                <Td><Badge value={e.status} /></Td>
                <Td className="tabular-nums">{e.budget_usage?.model_calls ?? 0}</Td>
                <Td className="tabular-nums">{e.budget_usage?.tool_calls ?? 0}</Td>
                <Td>{duration(e.duration_ms)}</Td>
                <Td className="text-xs text-slate-500">{dateTime(e.created_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
        {data && <div className="mt-2 text-xs text-slate-500">{data.total} execution(s)</div>}
      </Card>
    </div>
  );
}
