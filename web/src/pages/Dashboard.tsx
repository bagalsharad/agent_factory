import { Link } from "react-router-dom";
import { Badge, Card, Empty, ErrorBox, PageHeader, Spinner, Stat, Table, Td } from "../components/ui";
import { duration, shortId, timeAgo } from "../lib/format";
import { useApi } from "../lib/hooks";

export default function Dashboard() {
  const { data, error, loading } = useApi<any>("/api/v1/dashboard", { interval: 4000 });
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const providersUp = data.providers.filter((p: any) => p.status === "up").length;
  return (
    <div className="space-y-6">
      <PageHeader title="Dashboard" subtitle="Design, Build, Test, Govern, and Deploy Intelligent Agents." />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total agents" value={data.agents.total} hint={<Link className="text-indigo-600" to="/agents">Open catalog →</Link>} />
        <Stat label="Published agents" value={data.agents.published} tone={data.agents.published ? "good" : undefined} />
        <Stat label="Active executions" value={data.executions.active}
          hint={data.pending_approvals.total ? <Link className="text-amber-600" to="/approvals">{data.pending_approvals.total} awaiting approval</Link> : "queued, running or paused"} />
        <Stat label="Failed executions" value={data.executions.failed} tone={data.executions.failed ? "bad" : undefined} hint="failed or budget exceeded" />
      </div>
      <div className="grid gap-6 lg:grid-cols-3">
        <Card title="Recent executions" className="lg:col-span-2" actions={<Link className="text-sm text-indigo-600" to="/executions">All executions →</Link>}>
          <Table head={["Execution", "Agent", "Status", "Duration", "Started"]} empty={!data.recent_executions.length}>
            {data.recent_executions.map((e: any) => (
              <tr key={e.id}>
                <Td><Link className="font-mono text-indigo-600" to={`/executions/${e.id}`}>{shortId(e.id)}</Link></Td>
                <Td>{e.agent_name} <span className="text-xs text-slate-500">v{e.version}</span></Td>
                <Td><Badge value={e.status} /></Td>
                <Td>{duration(e.duration_ms)}</Td>
                <Td className="text-slate-500">{timeAgo(e.created_at)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <div className="space-y-6">
          <Card title="Runtime health">
            <dl className="space-y-2 text-sm">
              <div className="flex justify-between"><dt>Worker</dt><dd><Badge value={data.runtime.worker_healthy ? "ok" : data.runtime.worker_mode === "external" ? "external" : "down"} /></dd></div>
              <div className="flex justify-between"><dt>Database</dt><dd className="font-mono text-xs">{data.runtime.database}</dd></div>
              <div className="flex justify-between"><dt>Model providers up</dt><dd>{providersUp} / {data.providers.length}</dd></div>
            </dl>
          </Card>
          <Card title="Model availability" actions={<Link className="text-sm text-indigo-600" to="/models">Manage →</Link>}>
            <ul className="space-y-2 text-sm">
              {data.providers.map((p: any) => (
                <li key={p.provider} className="flex items-center justify-between gap-2">
                  <span>{p.provider} {!p.local && <span className="text-xs text-amber-600">(cloud)</span>}</span>
                  <Badge value={p.status} title={p.detail} />
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Evaluation status" actions={<Link className="text-sm text-indigo-600" to="/evaluations">Lab →</Link>}>
            {data.evaluations.recent_runs ? (
              <div className="grid grid-cols-3 gap-2 text-center text-sm">
                <div><div className="text-lg font-semibold text-emerald-600">{data.evaluations.passed}</div>passed</div>
                <div><div className="text-lg font-semibold text-rose-600">{data.evaluations.failed}</div>failed</div>
                <div><div className="text-lg font-semibold text-sky-600">{data.evaluations.running}</div>running</div>
              </div>
            ) : <Empty>No evaluation runs yet.</Empty>}
          </Card>
        </div>
      </div>
    </div>
  );
}
