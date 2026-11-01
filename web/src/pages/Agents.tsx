import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, ErrorBox, inputClass, PageHeader, Spinner, Table, Td } from "../components/ui";
import { post } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

const STATUSES = ["", "draft", "testing", "approved", "published", "deprecated", "disabled", "failed"];

export default function Agents() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState("");
  const params = new URLSearchParams({ limit: "200", ...(q ? { q } : {}), ...(status ? { status } : {}) });
  const { data, error, loading, reload } = useApi<any>(`/api/v1/agents?${params}`);
  const { can } = useSession();
  const navigate = useNavigate();
  const action = useAction(async (path: string, body?: unknown) => {
    const result = await post(path, body);
    reload();
    return result;
  });

  const duplicate = async (agent: any) => {
    const name = window.prompt("Name for the copy (lowercase, dashes)", `${agent.name}-copy`);
    if (!name) return;
    const created = await action.run(`/api/v1/agents/${agent.id}/duplicate`, { name });
    if (created) navigate(`/agents/${created.id}`);
  };

  return (
    <div className="space-y-4">
      <PageHeader title="Agent Catalog" subtitle="Every agent definition in this workspace, with lifecycle status and actions."
        actions={can("agents:write") && <Link to="/designer"><Button variant="primary">New agent</Button></Link>} />
      <div className="flex flex-wrap gap-2">
        <input aria-label="Search agents" className={`${inputClass} max-w-xs`} placeholder="Search name or description…"
          value={q} onChange={(e) => setQ(e.target.value)} />
        <select aria-label="Filter by status" className={`${inputClass} max-w-[12rem]`} value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUSES.map((s) => <option key={s} value={s}>{s || "any status"}</option>)}
        </select>
      </div>
      <ErrorBox error={error ?? action.error} />
      <Card>
        {loading && !data ? <Spinner /> : (
          <Table head={["Agent", "Version", "Status", "Active", "Workflow", "Workspace", "Updated", "Actions"]} empty={!data?.items.length}>
            {data?.items.map((a: any) => {
              const latest = a.latest_version ?? {};
              return (
                <tr key={a.id}>
                  <Td>
                    <Link to={`/agents/${a.id}`} className="font-medium text-indigo-600 hover:underline">{a.display_name}</Link>
                    <div className="max-w-md truncate text-xs text-slate-500" title={a.description}>{a.description}</div>
                  </Td>
                  <Td className="font-mono text-xs">{latest.version}</Td>
                  <Td><Badge value={latest.status} /></Td>
                  <Td className="font-mono text-xs">{a.active_version?.version ?? "—"}</Td>
                  <Td className="text-xs">{a.workflow_type}</Td>
                  <Td className="text-xs text-slate-500">default</Td>
                  <Td className="text-xs text-slate-500">{timeAgo(a.updated_at)}</Td>
                  <Td>
                    <div className="flex flex-wrap gap-1">
                      <Link to={`/agents/${a.id}`}><Button variant="ghost">View</Button></Link>
                      {can("agents:write") && <Link to={`/designer/${a.id}`}><Button variant="ghost">Edit</Button></Link>}
                      {can("agents:write") && <Button variant="ghost" onClick={() => duplicate(a)}>Duplicate</Button>}
                      {latest.executable && can("executions:run") && <Link to={`/agents/${a.id}?tab=run`}><Button variant="ghost">Execute</Button></Link>}
                      {latest.executable && can("evaluations:run") && (
                        <Button variant="ghost" onClick={() => action.run("/api/v1/evaluations", { agent_id: a.id }).then((r) => r && navigate(`/evaluations/${r.id}`))}>Evaluate</Button>
                      )}
                      {latest.status === "approved" && can("agents:publish") && (
                        <Button variant="ghost" onClick={() => action.run(`/api/v1/agents/${a.id}/publish`)}>Publish</Button>
                      )}
                      {a.active_version && can("agents:publish") && (
                        <Button variant="ghost" onClick={() => action.run(`/api/v1/agents/${a.id}/deprecate`)}>Deprecate</Button>
                      )}
                      {latest.status !== "disabled" && can("agents:disable") && (
                        <Button variant="ghost" className="text-rose-600" onClick={() => window.confirm(`Disable all versions of ${a.name}?`) && action.run(`/api/v1/agents/${a.id}/disable`)}>Disable</Button>
                      )}
                    </div>
                  </Td>
                </tr>
              );
            })}
          </Table>
        )}
      </Card>
    </div>
  );
}
