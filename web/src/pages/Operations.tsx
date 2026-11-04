import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ActivityChart, type ActivityBucket } from "../components/charts";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputClass, PageHeader, Spinner, Stat, Table, Td } from "../components/ui";
import { del, post } from "../lib/api";
import { duration, pct } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

const WINDOWS = [1, 24, 168];

export default function Operations() {
  const [search, setSearch] = useSearchParams();
  const hours = WINDOWS.includes(Number(search.get("hours"))) ? Number(search.get("hours")) : 24;
  const setHours = (h: number) => setSearch(h === 24 ? {} : { hours: String(h) }, { replace: true });
  const { data: metrics, error } = useApi<any>(`/api/v1/operations/metrics?hours=${hours}`, { interval: 10000 });
  const { data: alerts } = useApi<any>("/api/v1/operations/alerts", { interval: 10000 });
  const { data: providers } = useApi<any>("/api/v1/operations/providers", { interval: 15000 });
  const { data: slos, reload: reloadSlos } = useApi<any>("/api/v1/operations/slos");
  const { data: budgets, reload: reloadBudgets } = useApi<any>("/api/v1/operations/budgets");
  const buckets = hours === 1 ? 12 : hours === 168 ? 7 : 24;
  const { data: series } = useApi<{ items: ActivityBucket[] }>(`/api/v1/operations/timeseries?hours=${hours}&buckets=${buckets}`, { interval: 15000 });
  const { can } = useSession();
  if (error) return <ErrorBox error={error} />;
  if (!metrics) return <Spinner />;
  const w = metrics.workspace;
  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Operate"
        title="Operations"
        subtitle="Production executions only (evaluation runs are excluded). Computed from execution records."
        actions={<div className="flex rounded-lg border border-slate-200 bg-white p-0.5 dark:border-white/10 dark:bg-white/5" role="radiogroup" aria-label="Time window">{WINDOWS.map((h) => (
          <button key={h} role="radio" aria-checked={h === hours} onClick={() => setHours(h)}
            className={h === hours ? "rounded-md bg-slate-900 px-3 py-1 text-sm font-medium text-white dark:bg-white dark:text-slate-900" : "rounded-md px-3 py-1 text-sm text-slate-500 hover:text-slate-900 dark:hover:text-white"}>
            {h === 168 ? "7 days" : h === 1 ? "1 hour" : "24 hours"}</button>
        ))}</div>}
      />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-8">
        <Stat label="Executions" value={w.executions} />
        <Stat label="Success rate" value={pct(w.success_rate)} tone={w.success_rate == null ? undefined : w.success_rate >= 0.95 ? "good" : "bad"} />
        <Stat label="Failure rate" value={pct(w.failure_rate)} />
        <Stat label="Retry rate" value={pct(w.retry_rate)} hint={`${w.retries} retries`} />
        <Stat label="Timeout rate" value={pct(w.timeout_rate)} />
        <Stat label="Latency" value={duration(w.p50_latency_ms)} hint={`p95 ${duration(w.p95_latency_ms)}`} />
        <Stat label="Tokens" value={(w.input_tokens + w.output_tokens).toLocaleString()} />
        <Stat label="Cost" value={`$${(w.cost_usd ?? 0).toFixed(4)}`} hint={`${w.policy_violations} policy denials`} />
      </div>

      <Card title="Execution activity" subtitle={hours === 168 ? "Production runs per day, last 7 days" : hours === 1 ? "Production runs per 5 minutes, last hour" : "Production runs per hour, last 24 hours"} icon="chart">
        {series ? <ActivityChart items={series.items} hours={hours} /> : <Spinner />}
      </Card>

      <Card icon="alert" title={<span className="flex items-center gap-2">Alerts {alerts?.critical > 0 && <Badge value="critical" />}</span>}>
        {!alerts ? <Spinner /> : !alerts.items.length ? <Empty>Nothing needs an operator right now.</Empty> : (
          <ul className="space-y-1 text-sm">
            {alerts.items.map((a: any, i: number) => (
              <li key={i} className="flex flex-wrap items-center gap-2">
                <Badge value={a.severity === "critical" ? "failed" : "pending"} title={a.severity} />
                <span className="font-mono text-xs text-slate-500">{a.kind}</span>
                <span>{a.message}</span>
                {a.execution_id && <Link className="text-xs text-indigo-600" to={`/executions/${a.execution_id}`}>open execution →</Link>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card title="Agent health" icon="agents">
        <Table head={["Agent", "Executions", "Success", "p95 latency", "Retries", "Tokens", "Cost", "Approvals", "Eval pass rate"]} empty={!metrics.agents.length}>
          {metrics.agents.map((a: any) => (
            <tr key={a.agent_id}>
              <Td><Link className="text-indigo-600" to={`/agents/${a.agent_id}?tab=releases`}>{a.agent}</Link></Td>
              <Td>{a.executions}</Td>
              <Td>{pct(a.success_rate)}</Td>
              <Td>{duration(a.p95_latency_ms)}</Td>
              <Td>{a.retries}</Td>
              <Td>{(a.input_tokens + a.output_tokens).toLocaleString()}</Td>
              <Td>${(a.cost_usd ?? 0).toFixed(4)}</Td>
              <Td className="text-xs">{a.approvals.requested} requested · {pct(a.approvals.approval_rate)} approved</Td>
              <Td>{pct(a.evaluation_pass_rate)}</Td>
            </tr>
          ))}
        </Table>
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <SloCard slos={slos?.items} canEdit={can("settings:write")} reload={reloadSlos} agents={metrics.agents} />
        <BudgetCard budgets={budgets?.items} canEdit={can("settings:write")} reload={reloadBudgets} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Tool reliability" icon="tools">
          <Table head={["Tool", "Calls", "Success", "Failed", "Denied", "In doubt"]} empty={!Object.keys(w.tools).length}>
            {Object.entries<any>(w.tools).map(([name, t]) => (
              <tr key={name}><Td className="font-mono text-xs">{name}</Td><Td>{t.calls}</Td><Td>{pct(t.success_rate)}</Td>
                <Td>{t.failed}</Td><Td>{t.denied}</Td><Td className={t.in_doubt ? "text-rose-600" : ""}>{t.in_doubt}</Td></tr>
            ))}
          </Table>
        </Card>
        <Card title="Model providers" icon="models">
          {!providers ? <Spinner /> : (
            <Table head={["Provider", "Status", "Circuit", "Detail"]}>
              {providers.providers.map((p: any) => (
                <tr key={p.provider}><Td>{p.provider}</Td><Td><Badge value={p.status} /></Td>
                  <Td><Badge value={p.circuit?.state === "closed" ? "passed" : "failed"} title={p.circuit?.state} /> <span className="text-xs">{p.circuit?.state}</span></Td>
                  <Td className="text-xs text-slate-500">{p.detail}</Td></tr>
              ))}
            </Table>
          )}
        </Card>
      </div>
    </div>
  );
}

function SloCard({ slos, canEdit, reload, agents }: { slos?: any[]; canEdit: boolean; reload: () => void; agents: any[] }) {
  const [form, setForm] = useState({ name: "", metric: "success_rate", objective: "0.99", agent_id: "", window_hours: "168" });
  const create = useAction(async () => {
    await post("/api/v1/operations/slos", { ...form, objective: Number(form.objective), window_hours: Number(form.window_hours), agent_id: form.agent_id || null });
    setForm({ ...form, name: "" });
    reload();
  });
  const remove = useAction(async (id: string) => { await del(`/api/v1/operations/slos/${id}`); reload(); });
  return (
    <Card title="Service-level objectives" icon="approvals">
      {!slos ? <Spinner /> : !slos.length ? <Empty>No SLOs yet.</Empty> : (
        <Table head={["SLO", "Metric", "Value", "Status", "Error budget", ""]}>
          {slos.map((s) => (
            <tr key={s.id}>
              <Td><div>{s.name}</div><div className="text-xs text-slate-500">{s.agent ?? "workspace"} · {s.window_hours}h</div></Td>
              <Td className="text-xs">{s.metric} {s.direction} {s.objective}</Td>
              <Td>{s.value ?? "—"}</Td>
              <Td><Badge value={s.compliant == null ? "pending" : s.compliant ? "passed" : "failed"} title={s.compliant == null ? "no data" : undefined} /></Td>
              <Td>{s.error_budget_remaining == null ? "—" : pct(s.error_budget_remaining)}</Td>
              <Td>{canEdit && <Button variant="ghost" onClick={() => remove.run(s.id)}>Delete</Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
      {canEdit && (
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
          <Field label="Name"><input className={inputClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} /></Field>
          <Field label="Metric"><select className={inputClass} value={form.metric} onChange={(e) => setForm({ ...form, metric: e.target.value })}>
            {["success_rate", "error_rate", "p95_latency_ms", "evaluation_pass_rate"].map((m) => <option key={m}>{m}</option>)}</select></Field>
          <Field label="Objective"><input className={inputClass} value={form.objective} onChange={(e) => setForm({ ...form, objective: e.target.value })} /></Field>
          <Field label="Agent"><select className={inputClass} value={form.agent_id} onChange={(e) => setForm({ ...form, agent_id: e.target.value })}>
            <option value="">workspace</option>{agents.map((a) => <option key={a.agent_id} value={a.agent_id}>{a.agent}</option>)}</select></Field>
          <div className="flex items-end"><Button variant="primary" busy={create.pending} disabled={!form.name} onClick={() => create.run()}>Add SLO</Button></div>
        </div>
      )}
      <ErrorBox error={create.error ?? remove.error} />
    </Card>
  );
}

function BudgetCard({ budgets, canEdit, reload }: { budgets?: any[]; canEdit: boolean; reload: () => void }) {
  const [form, setForm] = useState({ period: "month", max_cost_usd: "", max_tokens: "", action: "block" });
  const create = useAction(async () => {
    await post("/api/v1/operations/budgets", {
      period: form.period, action: form.action,
      max_cost_usd: form.max_cost_usd ? Number(form.max_cost_usd) : null,
      max_tokens: form.max_tokens ? Number(form.max_tokens) : null,
    });
    reload();
  });
  const remove = useAction(async (id: string) => { await del(`/api/v1/operations/budgets/${id}`); reload(); });
  return (
    <Card title="Cost budgets" icon="layers">
      {!budgets ? <Spinner /> : !budgets.length ? <Empty>No budgets. Local models cost $0; token budgets still apply.</Empty> : (
        <Table head={["Scope", "Limit", "Used", "State", ""]}>
          {budgets.map((b) => (
            <tr key={b.id}>
              <Td className="text-xs">{b.agent_id ? "agent" : "workspace"} · per {b.period} · {b.action}</Td>
              <Td className="text-xs">{b.max_cost_usd != null && `$${b.max_cost_usd}`} {b.max_tokens != null && `${b.max_tokens.toLocaleString()} tokens`}</Td>
              <Td>{pct(b.used)}</Td>
              <Td><Badge value={b.state === "ok" ? "passed" : b.state === "warning" ? "pending" : "failed"} title={b.state} /></Td>
              <Td>{canEdit && <Button variant="ghost" onClick={() => remove.run(b.id)}>Delete</Button>}</Td>
            </tr>
          ))}
        </Table>
      )}
      {canEdit && (
        <div className="mt-3 grid grid-cols-2 gap-2 md:grid-cols-5">
          <Field label="Period"><select className={inputClass} value={form.period} onChange={(e) => setForm({ ...form, period: e.target.value })}><option>day</option><option>month</option></select></Field>
          <Field label="Max cost (USD)"><input className={inputClass} value={form.max_cost_usd} onChange={(e) => setForm({ ...form, max_cost_usd: e.target.value })} /></Field>
          <Field label="Max tokens"><input className={inputClass} value={form.max_tokens} onChange={(e) => setForm({ ...form, max_tokens: e.target.value })} /></Field>
          <Field label="When exhausted"><select className={inputClass} value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })}><option>block</option><option>warn</option></select></Field>
          <div className="flex items-end"><Button variant="primary" busy={create.pending} disabled={!form.max_cost_usd && !form.max_tokens} onClick={() => create.run()}>Add budget</Button></div>
        </div>
      )}
      <ErrorBox error={create.error ?? remove.error} />
    </Card>
  );
}
