import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import WorkflowGraph from "../components/WorkflowGraph";
import { DependenciesTab, ModelComparisonCard, PolicySimulatorCard, ReleasesTab } from "./AgentReleases";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputClass, JsonView, PageHeader, Spinner, Table, Tabs, Td } from "../components/ui";
import { post } from "../lib/api";
import { dateTime, duration, exampleFromSchema, pct, shortId, timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

const TABS = ["overview", "specification", "tools", "workflow", "model", "permissions", "versions", "evaluations", "releases", "dependencies", "executions", "run"];

export default function AgentDetail() {
  const { id } = useParams();
  const [search, setSearch] = useSearchParams();
  const tab = search.get("tab") ?? "overview";
  const { data: agent, error, loading, reload } = useApi<any>(`/api/v1/agents/${id}`);
  const { can } = useSession();
  const navigate = useNavigate();
  const act = useAction(async (path: string, body?: unknown) => {
    const result = await post(path, body);
    reload();
    return result;
  });

  if (loading && !agent) return <Spinner />;
  if (error) return <ErrorBox error={error} />;
  if (!agent) return null;
  const spec = agent.spec.spec;
  const latest = agent.latest_version;

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="Agent"
        title={<span className="flex items-center gap-3">
          <span className="brand-gradient grid h-9 w-9 place-items-center rounded-xl text-sm font-semibold text-white shadow-md shadow-brand-600/25">
            {(agent.display_name || agent.name).slice(0, 1).toUpperCase()}</span>
          {agent.display_name}<Badge value={latest?.status} /></span>}
        subtitle={<><span className="font-mono">{agent.name}</span> · latest v{latest?.version} · active {agent.active_version ? `v${agent.active_version.version}` : "none"} · template {agent.template ?? "—"}</>}
        actions={<>
          {can("agents:write") && <Link to={`/designer/${agent.id}`}><Button>Edit</Button></Link>}
          {can("agents:write") && ["draft", "failed", "testing"].includes(latest?.status) && (
            <Button busy={act.pending} onClick={() => act.run(`/api/v1/agents/${agent.id}/build`)}>Validate &amp; build</Button>
          )}
          {latest?.executable && can("evaluations:run") && (
            <Button busy={act.pending} onClick={() => act.run("/api/v1/evaluations", { agent_id: agent.id }).then((r) => r && navigate(`/evaluations/${r.id}`))}>Evaluate</Button>
          )}
          {latest?.status === "approved" && can("agents:publish") && (
            <Button variant="primary" busy={act.pending} onClick={() => act.run(`/api/v1/agents/${agent.id}/publish`)}>Publish v{latest.version}</Button>
          )}
          {latest?.executable && can("executions:run") && <Button variant="primary" onClick={() => setSearch({ tab: "run" })}>Execute</Button>}
        </>}
      />
      <ErrorBox error={act.error} />
      <Tabs tabs={TABS.map((t) => ({ id: t, label: t[0].toUpperCase() + t.slice(1) }))} active={tab} onChange={(t) => setSearch({ tab: t })} />
      {tab === "overview" && <Overview agent={agent} spec={spec} />}
      {tab === "specification" && <Card title="Specification (agentfactory/v1)"><JsonView value={agent.spec_yaml} maxHeight="40rem" /></Card>}
      {tab === "tools" && <ToolsTab spec={spec} />}
      {tab === "workflow" && <WorkflowTab agentId={agent.id} />}
      {tab === "model" && <Card title="Model configuration"><JsonView value={spec.model} /></Card>}
      {tab === "permissions" && <div className="space-y-4"><PermissionsTab spec={spec} /><PolicySimulatorCard agent={agent} /></div>}
      {tab === "versions" && <VersionsTab agent={agent} onChange={reload} />}
      {tab === "evaluations" && <EvaluationsTab agentId={agent.id} version={latest?.version} />}
      {tab === "releases" && <ReleasesTab agent={agent} />}
      {tab === "dependencies" && <DependenciesTab agent={agent} version={latest?.version} />}
      {tab === "executions" && <ExecutionsTab agentId={agent.id} />}
      {tab === "run" && <RunTab agent={agent} />}
    </div>
  );
}

function Overview({ agent, spec }: { agent: any; spec: any }) {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Description" className="lg:col-span-2">
        <p className="text-sm">{agent.description || "—"}</p>
        <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Objective</h3>
        <p className="text-sm">{spec.objective}</p>
        <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Instructions</h3>
        <pre className="whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-300">{spec.instructions || "—"}</pre>
      </Card>
      <Card title="At a glance">
        <dl className="space-y-2 text-sm">
          <Row k="Workflow" v={spec.workflow.type} />
          <Row k="Model" v={`${spec.model.provider} / ${spec.model.model}`} />
          <Row k="Tools" v={spec.tools?.length ?? 0} />
          <Row k="Write operations" v={spec.policies?.allow_write_operations ? "allowed (approval-gated)" : "forbidden"} />
          <Row k="External network" v={spec.policies?.external_network ? "allowlisted hosts" : "disabled"} />
          <Row k="Max steps / timeout" v={`${spec.execution_limits.max_steps} / ${spec.execution_limits.timeout_seconds}s`} />
          <Row k="Evaluation cases" v={spec.evaluation?.cases?.length ?? 0} />
          <Row k="Versions" v={agent.version_count} />
          <Row k="Last execution" v={timeAgo(agent.last_execution_at)} />
        </dl>
      </Card>
    </div>
  );
}

function Row({ k, v }: { k: string; v: any }) {
  return <div className="flex justify-between gap-3"><dt className="text-slate-500">{k}</dt><dd className="text-right">{String(v)}</dd></div>;
}

function ToolsTab({ spec }: { spec: any }) {
  const { data: tools } = useApi<any[]>("/api/v1/tools");
  const byName = useMemo(() => Object.fromEntries((tools ?? []).map((t) => [t.name, t])), [tools]);
  return (
    <Card title="Approved tools">
      <Table head={["Tool", "Risk", "Side effects", "Approval class", "Permissions", "Timeout"]} empty={!spec.tools?.length}>
        {spec.tools?.map((ref: any) => {
          const t = byName[ref.name] ?? {};
          return (
            <tr key={ref.name}>
              <Td><div className="font-mono text-xs">{ref.name}{ref.version ? `@${ref.version}` : ""}</div><div className="text-xs text-slate-500">{t.description}</div></Td>
              <Td><Badge value={t.risk} /></Td>
              <Td>{t.side_effects}{t.network ? " · network" : ""}</Td>
              <Td className="text-xs">{t.action_class ?? "—"}</Td>
              <Td className="text-xs">{t.permissions?.join(", ")}</Td>
              <Td className="text-xs">{t.timeout_seconds}s</Td>
            </tr>
          );
        })}
      </Table>
    </Card>
  );
}

function WorkflowTab({ agentId }: { agentId: string }) {
  const { data } = useApi<any>(`/api/v1/agents/${agentId}/workflow`);
  const [selected, setSelected] = useState<string | null>(null);
  if (!data) return <Spinner />;
  const node = data.nodes.find((n: any) => n.id === selected);
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title={`Workflow graph · ${data.pattern.replaceAll("_", " ")}`} className="lg:col-span-2">
        <WorkflowGraph graph={data} selected={selected} onSelect={setSelected} />
      </Card>
      <Card title="Node">{node ? <JsonView value={node} /> : <Empty>Select a node to inspect it.</Empty>}</Card>
    </div>
  );
}

function PermissionsTab({ spec }: { spec: any }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Policies"><JsonView value={spec.policies} /></Card>
      <Card title="Human approval"><JsonView value={spec.human_approval} /></Card>
      <Card title="Execution limits"><JsonView value={spec.execution_limits} /></Card>
      <Card title="Memory"><JsonView value={spec.memory} /></Card>
    </div>
  );
}

function VersionsTab({ agent, onChange }: { agent: any; onChange: () => void }) {
  const { can } = useSession();
  const [base, setBase] = useState(agent.versions[1]?.version ?? agent.versions[0]?.version);
  const [other, setOther] = useState(agent.versions[0]?.version);
  const compare = useApi<any>(base && other && base !== other ? `/api/v1/agents/${agent.id}/compare?base=${base}&other=${other}` : null);
  const act = useAction(async (path: string, body?: unknown) => {
    const r = await post(path, body);
    onChange();
    return r;
  });
  return (
    <div className="space-y-4">
      <ErrorBox error={act.error} />
      <Card title="Version history">
        <Table head={["Version", "Status", "Spec hash", "Created", "Published", "Release notes", "Actions"]}>
          {agent.versions.map((v: any) => (
            <tr key={v.id}>
              <Td className="font-mono">{v.version}{agent.active_version?.id === v.id && <span className="ml-1 text-xs text-emerald-600">active</span>}</Td>
              <Td><Badge value={v.status} /></Td>
              <Td className="font-mono text-xs">{v.spec_hash.slice(0, 12)}</Td>
              <Td className="text-xs">{dateTime(v.created_at)}</Td>
              <Td className="text-xs">{dateTime(v.published_at)}</Td>
              <Td className="max-w-xs truncate text-xs">{v.release_notes}</Td>
              <Td>
                <div className="flex gap-1">
                  {["published", "deprecated"].includes(v.status) && v.published_at && agent.active_version?.id !== v.id && can("agents:publish") && (
                    <Button variant="ghost" onClick={() => act.run(`/api/v1/agents/${agent.id}/rollback`, { version: v.version })}>Roll back to</Button>
                  )}
                  {v.status === "approved" && can("agents:publish") && (
                    <Button variant="ghost" onClick={() => act.run(`/api/v1/agents/${agent.id}/publish`, { version: v.version })}>Publish</Button>
                  )}
                  {v.status === "disabled" && can("agents:disable") && (
                    <Button variant="ghost" onClick={() => act.run(`/api/v1/agents/${agent.id}/enable`, { version: v.version })}>Re-enable</Button>
                  )}
                </div>
              </Td>
            </tr>
          ))}
        </Table>
      </Card>
      {agent.versions.length > 1 && (
        <Card title="Compare versions" actions={<>
          <select aria-label="Base version" className={inputClass} value={base} onChange={(e) => setBase(e.target.value)}>
            {agent.versions.map((v: any) => <option key={v.id}>{v.version}</option>)}
          </select>
          <span className="text-sm">→</span>
          <select aria-label="Other version" className={inputClass} value={other} onChange={(e) => setOther(e.target.value)}>
            {agent.versions.map((v: any) => <option key={v.id}>{v.version}</option>)}
          </select>
        </>}>
          {compare.data ? (
            <div className="space-y-3">
              <div className="grid gap-3 md:grid-cols-2">
                {[base, other].map((ver) => {
                  const ev = compare.data.evaluations[ver];
                  return <div key={ver} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/50">
                    <div className="font-mono font-semibold">v{ver}</div>
                    {ev ? <div>last evaluation: <Badge value={ev.passed ? "passed" : "failed"} /> pass rate {pct(ev.metrics?.pass_rate)}</div> : <div className="text-slate-500">not evaluated</div>}
                  </div>;
                })}
              </div>
              <Table head={["Path", "Change", "Base", "Other"]} empty={!compare.data.changes.length}>
                {compare.data.changes.map((c: any, i: number) => (
                  <tr key={i}>
                    <Td className="font-mono text-xs">{c.path}</Td>
                    <Td><Badge value={c.op} /></Td>
                    <Td className="max-w-xs break-all font-mono text-xs">{c.old === undefined ? "" : JSON.stringify(c.old)}</Td>
                    <Td className="max-w-xs break-all font-mono text-xs">{c.new === undefined ? "" : JSON.stringify(c.new)}</Td>
                  </tr>
                ))}
              </Table>
            </div>
          ) : <Empty>Pick two different versions.</Empty>}
        </Card>
      )}
    </div>
  );
}

function ReleaseGateCard({ agentId, version }: { agentId: string; version?: string }) {
  const { data: gate, error } = useApi<any>(`/api/v1/agents/${agentId}/release-gate${version ? `?version=${version}` : ""}`);
  if (error) return <ErrorBox error={error} />;
  if (!gate) return <Spinner />;
  const regression = gate.evaluation?.regression;
  return (
    <Card title={<span className="flex items-center gap-2">Release gate · v{gate.version} <Badge value={gate.publishable ? "passed" : "failed"} /></span>}>
      <div className="space-y-2 text-sm">
        {gate.publishable
          ? <p className="text-emerald-700">This version can be published.</p>
          : <ul className="list-disc space-y-1 pl-5 text-rose-700">{gate.blockers.map((b: string) => <li key={b}>{b}</li>)}</ul>}
        {gate.evaluation && (
          <div className="flex flex-wrap gap-3 text-xs">
            {Object.entries<boolean>(gate.evaluation.checks ?? {}).map(([k, v]) => <span key={k}>{k.replaceAll("_", " ")} <Badge value={v ? "passed" : "failed"} /></span>)}
          </div>
        )}
        {regression && (
          <p className="text-xs text-slate-500">
            {regression.baseline_version ? `Compared with published v${regression.baseline_version} on ${regression.compared} case(s)` : "No published baseline yet"}
            {regression.regressed?.length > 0 && <> · regressed: <span className="font-mono text-rose-600">{regression.regressed.join(", ")}</span> ({regression.mode})</>}
          </p>
        )}
        {gate.evaluation && <Link className="text-xs text-indigo-600" to={`/evaluations/${gate.evaluation.id}`}>Latest evaluation for this specification →</Link>}
      </div>
    </Card>
  );
}

function EvaluationsTab({ agentId, version }: { agentId: string; version?: string }) {
  const { data } = useApi<any>(`/api/v1/evaluations?agent_id=${agentId}`);
  return (
    <div className="space-y-4">
      <ReleaseGateCard agentId={agentId} version={version} />
      <ModelComparisonCard agentId={agentId} />
      <Card title="Evaluation history">
        <Table head={["Run", "Version", "Result", "Pass rate", "Avg latency", "Started"]} empty={!data?.items.length}>
          {data?.items.map((r: any) => (
            <tr key={r.id}>
              <Td><Link className="font-mono text-indigo-600" to={`/evaluations/${r.id}`}>{shortId(r.id)}</Link></Td>
              <Td className="font-mono text-xs">{r.agent_version}</Td>
              <Td><Badge value={r.status === "completed" ? (r.passed ? "passed" : "failed") : r.status} /></Td>
              <Td>{pct(r.metrics?.pass_rate)}</Td>
              <Td>{duration(r.metrics?.avg_latency_ms)}</Td>
              <Td className="text-xs">{timeAgo(r.created_at)}</Td>
            </tr>
          ))}
        </Table>
      </Card>
    </div>
  );
}

function ExecutionsTab({ agentId }: { agentId: string }) {
  const { data } = useApi<any>(`/api/v1/executions?agent_id=${agentId}&limit=50`, { interval: 4000 });
  return (
    <Card title="Execution history">
      <Table head={["Execution", "Version", "Mode", "Status", "Duration", "Created"]} empty={!data?.items.length}>
        {data?.items.map((e: any) => (
          <tr key={e.id}>
            <Td><Link className="font-mono text-indigo-600" to={`/executions/${e.id}`}>{shortId(e.id)}</Link></Td>
            <Td className="font-mono text-xs">{e.version}</Td>
            <Td className="text-xs">{e.mode}</Td>
            <Td><Badge value={e.status} /></Td>
            <Td>{duration(e.duration_ms)}</Td>
            <Td className="text-xs">{timeAgo(e.created_at)}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function RunTab({ agent }: { agent: any }) {
  const navigate = useNavigate();
  const executable = agent.versions.filter((v: any) => v.executable);
  const [version, setVersion] = useState<string>("");
  const examples = agent.spec.spec.evaluation?.cases ?? [];
  const [input, setInput] = useState(JSON.stringify(examples[0]?.input ?? exampleFromSchema(agent.spec.spec.inputs), null, 2));
  const [parseError, setParseError] = useState<string>();
  const run = useAction(async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(input);
      setParseError(undefined);
    } catch (e) {
      setParseError(String(e));
      return;
    }
    const created = await post("/api/v1/executions", { agent_id: agent.id, version: version || undefined, input: parsed });
    navigate(`/executions/${created.id}`);
  });
  if (!executable.length) return <Card><Empty>No executable version yet. Validate and build the agent first.</Empty></Card>;
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Execute agent" className="lg:col-span-2">
        <div className="space-y-3">
          <Field label="Version" hint="Default: the active published version, otherwise the latest built version.">
            <select className={inputClass} value={version} onChange={(e) => setVersion(e.target.value)}>
              <option value="">default</option>
              {executable.map((v: any) => <option key={v.id} value={v.version}>{v.version} ({v.status})</option>)}
            </select>
          </Field>
          <Field label="Input (JSON)" hint="Validated against the agent's input schema before the execution is queued.">
            <textarea aria-label="Execution input" className={`${inputClass} h-48 font-mono`} value={input} onChange={(e) => setInput(e.target.value)} />
          </Field>
          {parseError && <div className="text-sm text-rose-600">{parseError}</div>}
          <ErrorBox error={run.error} />
          <Button variant="primary" busy={run.pending} onClick={() => run.run()}>Run</Button>
        </div>
      </Card>
      <Card title="Sample inputs">
        {examples.length ? (
          <ul className="space-y-2 text-sm">
            {examples.slice(0, 8).map((c: any) => (
              <li key={c.id}><button className="text-left text-indigo-600 hover:underline" onClick={() => setInput(JSON.stringify(c.input, null, 2))}>{c.name}</button></li>
            ))}
          </ul>
        ) : <Empty>No samples in the specification.</Empty>}
        <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Input schema</h3>
        <JsonView value={agent.spec.spec.inputs} maxHeight="14rem" />
      </Card>
    </div>
  );
}
