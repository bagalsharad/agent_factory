import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputClass, Spinner, Table, Td } from "../components/ui";
import { post, put } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export function ReleasesTab({ agent }: { agent: any }) {
  const { data, error, reload } = useApi<any>(`/api/v1/agents/${agent.id}/deployments`);
  const { can, me } = useSession();
  const published = agent.versions.filter((v: any) => ["published", "deprecated"].includes(v.status));
  const [form, setForm] = useState({ version: published[0]?.version ?? "", environment: "development", strategy: "standard", traffic_percent: "10" });
  const act = useAction(async (path: string, body?: unknown) => { const r = await post(path, body); reload(); return r; });
  const { data: artifact } = useApi<any>(form.version ? `/api/v1/agents/${agent.id}/versions/${form.version}/artifact` : null);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  const canRelease = can("agents:publish");
  return (
    <div className="space-y-4">
      <ErrorBox error={act.error} />
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        {data.environments.map((env: any) => (
          <Card key={env.environment} icon={env.environment === "production" ? "rocket" : "layers"}
            title={<span className="flex items-center gap-2"><span className="capitalize">{env.environment}</span>
              {(env.primary || env.canary) && <span className="h-2 w-2 rounded-full bg-emerald-500" title="serving" />}</span>}
            subtitle={env.requires_approval ? "Approval required" : "Self-service"}>
            <div className="space-y-2 text-sm">
              {env.promote_from && <div className="text-xs text-slate-500">Accepts artifacts promoted from {env.promote_from}</div>}
              <div>Primary: {env.primary ? <span className="font-mono">v{env.primary.version}</span> : <span className="text-slate-400">none</span>}</div>
              {env.canary && <div>Canary: <span className="font-mono">v{env.canary.version}</span> · {env.canary.traffic_percent}% of traffic</div>}
              {env.pending.map((d: any) => (
                <div key={d.id} className="rounded border border-amber-300 p-2 text-xs">
                  Pending v{d.version} ({d.strategy}) requested {timeAgo(d.created_at)}
                  {canRelease && (d.requested_by !== me?.user_id ? (
                    <div className="mt-1 flex gap-1">
                      <Button variant="primary" onClick={() => act.run(`/api/v1/deployments/${d.id}/decision`, { approve: true })}>Approve</Button>
                      <Button variant="ghost" className="text-rose-600" onClick={() => act.run(`/api/v1/deployments/${d.id}/decision`, { approve: false })}>Reject</Button>
                    </div>
                  ) : <div className="mt-1 text-slate-500">Another person must approve your request.</div>)}
                </div>
              ))}
              {canRelease && (env.primary || env.canary) && (
                <div className="flex flex-wrap gap-1">
                  {env.canary && <Button onClick={() => act.run(`/api/v1/agents/${agent.id}/environments/${env.environment}/promote-canary`)}>Promote canary</Button>}
                  <Button onClick={() => act.run(`/api/v1/agents/${agent.id}/environments/${env.environment}/rollback`, { reason: "rolled back from the dashboard" })}>Roll back</Button>
                </div>
              )}
            </div>
          </Card>
        ))}
      </div>
      {canRelease && (
        <Card title="Deploy a published version">
          {!published.length ? <Empty>Publish a version first: only published versions have release artifacts.</Empty> : (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
              <Field label="Version"><select className={inputClass} value={form.version} onChange={(e) => setForm({ ...form, version: e.target.value })}>
                {published.map((v: any) => <option key={v.id}>{v.version}</option>)}</select></Field>
              <Field label="Environment"><select className={inputClass} value={form.environment} onChange={(e) => setForm({ ...form, environment: e.target.value })}>
                {data.environments.map((e: any) => <option key={e.environment}>{e.environment}</option>)}</select></Field>
              <Field label="Strategy"><select className={inputClass} value={form.strategy} onChange={(e) => setForm({ ...form, strategy: e.target.value })}><option>standard</option><option>canary</option></select></Field>
              {form.strategy === "canary" && <Field label="Canary traffic %"><input className={inputClass} value={form.traffic_percent} onChange={(e) => setForm({ ...form, traffic_percent: e.target.value })} /></Field>}
              <div className="flex items-end"><Button variant="primary" busy={act.pending} onClick={() => act.run(`/api/v1/agents/${agent.id}/deployments`, {
                version: form.version, environment: form.environment, strategy: form.strategy,
                traffic_percent: form.strategy === "canary" ? Number(form.traffic_percent) : 100 })}>Deploy</Button></div>
            </div>
          )}
          {artifact && (
            <p className="mt-2 text-xs text-slate-500">Artifact <span className="font-mono">{artifact.artifact_hash.slice(0, 16)}…</span> ·
              integrity <Badge value={artifact.verification.valid ? "passed" : "failed"} /> {artifact.verification.problems.join("; ")}</p>
          )}
        </Card>
      )}
      <Card title="Deployment history">
        <Table head={["Environment", "Version", "Strategy", "Status", "Requested", "Approved by", "Notes"]}
          empty={!data.environments.some((e: any) => e.history.length)}>
          {data.environments.flatMap((e: any) => e.history.map((d: any) => (
            <tr key={d.id}><Td>{d.environment}</Td><Td className="font-mono text-xs">v{d.version}</Td><Td className="text-xs">{d.strategy}{d.strategy === "canary" && ` ${d.traffic_percent}%`}</Td>
              <Td><Badge value={d.status} /></Td><Td className="text-xs">{timeAgo(d.created_at)}</Td><Td className="font-mono text-xs">{d.approved_by?.slice(0, 8) ?? "—"}</Td>
              <Td className="text-xs text-slate-500">{d.notes}</Td></tr>
          )))}
        </Table>
      </Card>
    </div>
  );
}

export function DependenciesTab({ agent, version }: { agent: any; version?: string }) {
  const { data, error } = useApi<any>(`/api/v1/agents/${agent.id}/dependencies${version ? `?version=${version}` : ""}`);
  const { can } = useSession();
  const [owners, setOwners] = useState(agent.ownership ?? {});
  const save = useAction(async () => { await put(`/api/v1/agents/${agent.id}/ownership`, owners); });
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  return (
    <div className="space-y-4">
      <Card title={`Dependencies of v${data.version}`}>
        {data.problems.length > 0 && <p className="mb-2 text-sm text-rose-700">{data.problems.length} dependency problem(s): revalidate before releasing.</p>}
        <Table head={["Kind", "Name", "Version at build", "State"]} empty={!data.items.length}>
          {data.items.map((d: any) => (
            <tr key={`${d.kind}:${d.name}`}><Td className="text-xs">{d.kind}</Td><Td className="font-mono text-xs">{d.name}</Td><Td className="font-mono text-xs">{d.version ?? "—"}</Td>
              <Td><Badge value={d.state === "ok" ? "passed" : "failed"} title={d.state} /> <span className="text-xs text-slate-500">{d.detail}</span></Td></tr>
          ))}
        </Table>
      </Card>
      <Card title="Ownership and support">
        <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
          {["business_owner", "technical_contact", "escalation_contact"].map((k) => (
            <Field key={k} label={k.replaceAll("_", " ")}><input className={inputClass} disabled={!can("agents:write")} value={owners[k] ?? ""} onChange={(e) => setOwners({ ...owners, [k]: e.target.value })} /></Field>
          ))}
          {can("agents:write") && <div className="flex items-end"><Button busy={save.pending} onClick={() => save.run()}>Save</Button></div>}
        </div>
        <ErrorBox error={save.error} />
      </Card>
    </div>
  );
}

export function ModelComparisonCard({ agentId }: { agentId: string }) {
  const [models, setModels] = useState("mock/mock-default\nollama/qwen3:8b");
  const [result, setResult] = useState<any>();
  const run = useAction(async () => setResult(await post<any>("/api/v1/evaluations/model-comparison", {
    agent_id: agentId,
    models: models.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
      const [provider, ...rest] = l.split("/");
      return { provider, model: rest.join("/") };
    }),
  })));
  return (
    <Card title="Compare models on this suite">
      <p className="mb-2 text-xs text-slate-500">Comparison runs never gate publishing or change the version's status. Real models can take minutes per case.</p>
      <div className="flex flex-col gap-2 md:flex-row">
        <textarea className={inputClass + " font-mono"} rows={3} value={models} onChange={(e) => setModels(e.target.value)} />
        <div><Button variant="primary" busy={run.pending} onClick={() => run.run()}>Compare</Button></div>
      </div>
      <ErrorBox error={run.error} />
      {result && (
        <Table head={["Model", "Gates", "Security", "Pass rate", "Schema", "Tool selection", "p95", "Tokens", "Cost"]}>
          {result.models.map((m: any) => (
            <tr key={m.run_id}><Td className="font-mono text-xs">{m.model}</Td><Td><Badge value={m.gates_passed ? "passed" : "failed"} /></Td>
              <Td><Badge value={m.security_gates_passed ? "passed" : "failed"} /></Td>
              <Td>{m.metrics.pass_rate}</Td><Td>{m.metrics.output_schema_compliance ?? "—"}</Td><Td>{m.metrics.correct_tool_selection ?? "—"}</Td>
              <Td>{m.metrics.p95_latency_ms ?? "—"}</Td><Td>{(m.metrics.input_tokens ?? 0) + (m.metrics.output_tokens ?? 0)}</Td><Td>${(m.metrics.cost_usd ?? 0).toFixed(4)}</Td></tr>
          ))}
        </Table>
      )}
    </Card>
  );
}

export function PolicySimulatorCard({ agent }: { agent: any }) {
  const tools: string[] = (agent.spec.spec.tools ?? []).map((t: any) => (typeof t === "string" ? t : t.name));
  const [tool, setTool] = useState(tools[0] ?? "");
  const [args, setArgs] = useState("{}");
  const [role, setRole] = useState("");
  const [result, setResult] = useState<any>();
  const sim = useAction(async () => setResult(await post<any>("/api/v1/policies/simulate", {
    agent_id: agent.id, tool, arguments: JSON.parse(args || "{}"), as_role: role || null,
  })));
  const d = result?.decision;
  return (
    <Card title="Policy simulator">
      <p className="mb-2 text-xs text-slate-500">Asks the real policy engine what it would decide. Nothing is executed.</p>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-4">
        <Field label="Tool"><input className={inputClass} list="sim-tools" value={tool} onChange={(e) => setTool(e.target.value)} />
          <datalist id="sim-tools">{tools.map((t) => <option key={t} value={t} />)}</datalist></Field>
        <Field label="Arguments (JSON)"><input className={inputClass + " font-mono"} value={args} onChange={(e) => setArgs(e.target.value)} /></Field>
        <Field label="As role"><select className={inputClass} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="">me</option>{["admin", "developer", "operator", "evaluator", "viewer"].map((r) => <option key={r}>{r}</option>)}</select></Field>
        <div className="flex items-end"><Button busy={sim.pending} disabled={!tool} onClick={() => sim.run()}>Simulate</Button></div>
      </div>
      <ErrorBox error={sim.error} />
      {d && (
        <div className="mt-3 text-sm">
          <Badge value={d.effect === "allow" ? "passed" : d.effect === "deny" ? "failed" : "pending"} title={d.effect} /> <b>{d.effect}</b>
          <span className="ml-2 font-mono text-xs text-slate-500">{d.rules.join(", ")}</span>
          <ul className="mt-1 list-disc pl-5 text-xs">{d.reasons.map((r: string) => <li key={r}>{r}</li>)}</ul>
        </div>
      )}
    </Card>
  );
}
