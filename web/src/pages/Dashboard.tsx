import { Link } from "react-router-dom";
import { ActivityChart, type ActivityBucket } from "../components/charts";
import { icons, type IconName } from "../components/icons";
import { Badge, Button, Card, Empty, ErrorBox, Spinner, Stat, Table, Td } from "../components/ui";
import { duration, pct, shortId, timeAgo } from "../lib/format";
import { useApi } from "../lib/hooks";

const STAGES: { key: string; label: string; icon: IconName; hint: string }[] = [
  { key: "draft", label: "Draft", icon: "designer", hint: "being designed" },
  { key: "testing", label: "Built", icon: "layers", hint: "validated, under test" },
  { key: "approved", label: "Approved", icon: "evaluations", hint: "passed every gate" },
  { key: "published", label: "Published", icon: "rocket", hint: "serving" },
];

export default function Dashboard() {
  const { data, error, loading } = useApi<any>("/api/v1/dashboard", { interval: 4000 });
  const { data: series } = useApi<{ items: ActivityBucket[] }>("/api/v1/operations/timeseries?hours=24&buckets=24", { interval: 15000 });
  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const providersUp = data.providers.filter((p: any) => p.status === "up").length;
  const byStatus = data.agents.versions_by_status ?? {};
  const items = series?.items ?? [];
  const trend = (pick: (b: ActivityBucket) => number) => {
    const values = items.map(pick);
    const size = Math.ceil(values.length / 12) || 1;  // 12-point trend
    return Array.from({ length: Math.ceil(values.length / size) }, (_, i) => values.slice(i * size, (i + 1) * size).reduce((a, b) => a + b, 0));
  };
  const runs24 = items.reduce((n, b) => n + b.completed + b.failed, 0);
  const ok24 = items.reduce((n, b) => n + b.completed, 0);
  const evals = data.evaluations;

  return (
    <div className="space-y-6">
      <section className="animate-rise relative overflow-hidden rounded-3xl bg-slate-950 px-6 py-7 text-white shadow-xl sm:px-8">
        <div className="pointer-events-none absolute inset-0 [background-image:radial-gradient(500px_260px_at_10%_0%,rgb(99_102_241/0.45),transparent_70%),radial-gradient(420px_240px_at_95%_110%,rgb(217_70_239/0.35),transparent_70%)]" />
        <div className="pointer-events-none absolute inset-0 opacity-[0.08] [background-image:linear-gradient(rgb(255_255_255/0.6)_1px,transparent_1px),linear-gradient(90deg,rgb(255_255_255/0.6)_1px,transparent_1px)] [background-size:28px_28px]" />
        <div className="relative flex flex-wrap items-end justify-between gap-6">
          <div className="max-w-xl">
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-brand-400">Control plane</div>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight sm:text-4xl">Design, build, test, govern and deploy agents.</h1>
            <p className="mt-2 text-sm text-slate-300">Every agent is evaluated by independent checks, governed outside the model, and released through gated environments.</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/designer"><Button variant="primary" icon="plus">New agent</Button></Link>
            <Link to="/operations"><Button className="border-white/15 bg-white/10 text-white hover:bg-white/15" icon="operations">Operations</Button></Link>
          </div>
        </div>
        <ol className="relative mt-7 grid grid-cols-2 gap-2 sm:grid-cols-4 [&>*]:min-w-0" aria-label="Agent versions by lifecycle stage">
          {STAGES.map((stage, i) => {
            const Icon = icons[stage.icon];
            return (
              <li key={stage.key} className="relative rounded-2xl border border-white/10 bg-white/[0.06] p-4 backdrop-blur">
                <div className="flex items-center justify-between text-xs text-slate-300">
                  <span className="flex items-center gap-1.5"><Icon className="h-3.5 w-3.5 text-brand-400" />{stage.label}</span>
                  <span className="text-slate-500">{i + 1}/4</span>
                </div>
                <div className="mt-2 text-3xl font-semibold">{byStatus[stage.key] ?? 0}</div>
                <div className="text-xs text-slate-400">{stage.hint}</div>
                {i < STAGES.length - 1 && <icons.arrowRight className="absolute top-1/2 -right-2.5 z-10 hidden h-4 w-4 -translate-y-1/2 text-slate-500 sm:block" />}
              </li>
            );
          })}
        </ol>
      </section>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Total agents" value={data.agents.total} icon="agents" hint={<Link className="text-brand-600 dark:text-brand-400" to="/agents">Open catalog →</Link>} />
        <Stat label="Runs, last 24 h" value={runs24} icon="executions" trend={trend((b) => b.completed + b.failed)}
          hint={runs24 ? `${pct(ok24 / runs24)} completed` : "no production runs"} />
        <Stat label="Active executions" value={data.executions.active} icon="clock"
          hint={data.pending_approvals.total ? <Link className="text-amber-600" to="/approvals">{data.pending_approvals.total} awaiting approval</Link> : "queued, running or paused"} />
        <Stat label="Failed executions" value={data.executions.failed} icon="alert" tone={data.executions.failed ? "bad" : undefined}
          trend={trend((b) => b.failed)} hint="failed or budget exceeded" />
      </div>

      <Card title="Execution activity" subtitle="Production runs per hour, last 24 hours" icon="chart"
        actions={<Link className="text-sm text-brand-600 dark:text-brand-400" to="/operations">Details →</Link>}>
        {series ? <ActivityChart items={items} hours={24} /> : <Spinner />}
      </Card>

      <div className="grid gap-6 lg:grid-cols-3 [&>*]:min-w-0">
        <Card title="Recent executions" icon="executions" className="lg:col-span-2" actions={<Link className="text-sm text-brand-600 dark:text-brand-400" to="/executions">All executions →</Link>}>
          <Table head={["Execution", "Agent", "Status", "Duration", "Started"]} empty={!data.recent_executions.length}>
            {data.recent_executions.map((e: any) => (
              <tr key={e.id}>
                <Td><Link className="font-mono text-brand-600 dark:text-brand-400" to={`/executions/${e.id}`}>{shortId(e.id)}</Link></Td>
                <Td><span className="font-medium">{e.agent_name}</span> <span className="text-xs text-slate-500">v{e.version}</span></Td>
                <Td><Badge value={e.status} /></Td>
                <Td className="whitespace-nowrap">{duration(e.duration_ms)}</Td>
                <Td className="whitespace-nowrap text-slate-500">{timeAgo(e.created_at)}</Td>
              </tr>
            ))}
          </Table>
        </Card>
        <div className="space-y-6">
          <Card title="Runtime health" icon="monitor">
            <dl className="space-y-2.5 text-sm">
              <div className="flex items-center justify-between"><dt className="text-slate-500">Worker</dt><dd><Badge value={data.runtime.worker_healthy ? "ok" : data.runtime.worker_mode === "external" ? "external" : "down"} /></dd></div>
              <div className="flex items-center justify-between"><dt className="text-slate-500">Database</dt><dd className="font-mono text-xs">{data.runtime.database}</dd></div>
              <div className="flex items-center justify-between"><dt className="text-slate-500">Model providers up</dt><dd className="font-medium">{providersUp} / {data.providers.length}</dd></div>
            </dl>
          </Card>
          <Card title="Model availability" icon="models" actions={<Link className="text-sm text-brand-600 dark:text-brand-400" to="/models">Manage →</Link>}>
            <ul className="space-y-2 text-sm">
              {data.providers.map((p: any) => (
                <li key={p.provider} className="flex items-center justify-between gap-2">
                  <span>{p.provider} {!p.local && <span className="text-xs text-amber-600">(cloud)</span>}</span>
                  <Badge value={p.status} title={p.detail} />
                </li>
              ))}
            </ul>
          </Card>
          <Card title="Evaluation status" icon="evaluations" actions={<Link className="text-sm text-brand-600 dark:text-brand-400" to="/evaluations">Lab →</Link>}>
            {evals.recent_runs ? (
              <div className="grid grid-cols-3 gap-2 text-center text-sm">
                {[["passed", evals.passed, "text-emerald-600 dark:text-emerald-400", icons.check],
                  ["failed", evals.failed, "text-rose-600 dark:text-rose-400", icons.x],
                  ["running", evals.running, "text-sky-600 dark:text-sky-400", icons.clock]].map(([name, value, color, Icon]: any) => (
                  <div key={name} className="rounded-xl bg-slate-50 py-3 dark:bg-white/[0.03]">
                    <div className={`text-xl font-semibold ${color}`}>{value}</div>
                    <div className="mt-0.5 flex items-center justify-center gap-1 text-xs text-slate-500"><Icon className="h-3 w-3" />{name}</div>
                  </div>
                ))}
              </div>
            ) : <Empty>No evaluation runs yet.</Empty>}
          </Card>
        </div>
      </div>
    </div>
  );
}
