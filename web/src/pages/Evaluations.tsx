import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputClass, PageHeader, Spinner, Table, Tabs, Td } from "../components/ui";
import { post } from "../lib/api";
import { duration, pct, shortId, timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function Evaluations() {
  const [tab, setTab] = useState("runs");
  return (
    <div className="space-y-4">
      <PageHeader title="Evaluation Lab" subtitle="Repeatable, deterministic test suites gate publication. LLM judgement is never the sole quality or safety gate." />
      <Tabs tabs={[{ id: "runs", label: "Runs" }, { id: "compare", label: "Compare versions" }, { id: "suites", label: "Suites" }]} active={tab} onChange={setTab} />
      {tab === "runs" && <Runs />}
      {tab === "compare" && <Compare />}
      {tab === "suites" && <Suites />}
    </div>
  );
}

function AgentVersionPicker({ agentId, setAgentId, version, setVersion, label = "Version" }: {
  agentId: string; setAgentId: (v: string) => void; version: string; setVersion: (v: string) => void; label?: string;
}) {
  const { data: agents } = useApi<any>("/api/v1/agents?limit=200");
  const { data: versions } = useApi<any[]>(agentId ? `/api/v1/agents/${agentId}/versions` : null);
  return (
    <div className="grid gap-3 md:grid-cols-2">
      <Field label="Agent">
        <select className={inputClass} value={agentId} onChange={(e) => { setAgentId(e.target.value); setVersion(""); }}>
          <option value="">choose…</option>
          {agents?.items?.map((a: any) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
      </Field>
      <Field label={label}>
        <select className={inputClass} value={version} onChange={(e) => setVersion(e.target.value)} disabled={!agentId}>
          <option value="">latest built</option>
          {versions?.filter((v) => v.executable).map((v) => <option key={v.id} value={v.version}>{v.version} ({v.status})</option>)}
        </select>
      </Field>
    </div>
  );
}

function Runs() {
  const { data, loading } = useApi<any>("/api/v1/evaluations?limit=100", { interval: 4000 });
  const [agentId, setAgentId] = useState("");
  const [version, setVersion] = useState("");
  const navigate = useNavigate();
  const { can } = useSession();
  const start = useAction(async () => {
    const run = await post("/api/v1/evaluations", { agent_id: agentId, version: version || undefined });
    navigate(`/evaluations/${run.id}`);
  });
  return (
    <div className="space-y-4">
      {can("evaluations:run") && (
        <Card title="Run an evaluation suite">
          <AgentVersionPicker agentId={agentId} setAgentId={setAgentId} version={version} setVersion={setVersion} />
          <div className="mt-3 flex items-center gap-3">
            <Button variant="primary" disabled={!agentId} busy={start.pending} onClick={() => start.run()}>Start evaluation</Button>
            <span className="text-xs text-slate-500">Runs the suite declared in the agent specification (plus pinned evaluation datasets).</span>
          </div>
          <div className="mt-2"><ErrorBox error={start.error} /></div>
        </Card>
      )}
      <Card title="Evaluation runs">
        {loading && !data ? <Spinner /> : (
          <Table head={["Run", "Agent version", "Result", "Pass rate", "Schema compliance", "Unauthorized rejected", "Avg latency", "Started"]} empty={!data?.items.length}>
            {data?.items.map((r: any) => (
              <tr key={r.id}>
                <Td><Link className="font-mono text-indigo-600" to={`/evaluations/${r.id}`}>{shortId(r.id)}</Link></Td>
                <Td className="font-mono text-xs"><Link className="hover:underline" to={`/agents/${r.agent_id}`}>{r.agent_version}</Link></Td>
                <Td><Badge value={r.status === "completed" ? (r.passed ? "passed" : "failed") : r.status} /></Td>
                <Td>{pct(r.metrics?.pass_rate)}</Td>
                <Td>{pct(r.metrics?.output_schema_compliance)}</Td>
                <Td>{pct(r.metrics?.unauthorized_action_rejection)}</Td>
                <Td>{duration(r.metrics?.avg_latency_ms)}</Td>
                <Td className="text-xs">{timeAgo(r.created_at)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}

function Compare() {
  const [agentId, setAgentId] = useState("");
  const [base, setBase] = useState("");
  const [other, setOther] = useState("");
  const [result, setResult] = useState<any>(null);
  const compare = useAction(async () => setResult(await post("/api/v1/evaluations/compare", { agent_id: agentId, base, other })));
  return (
    <div className="space-y-4">
      <Card title="Compare two versions on the same suite">
        <AgentVersionPicker agentId={agentId} setAgentId={setAgentId} version={base} setVersion={setBase} label="Base version" />
        <div className="mt-3"><AgentVersionPickerOther agentId={agentId} version={other} setVersion={setOther} /></div>
        <Button className="mt-3" variant="primary" disabled={!agentId || !base || !other || base === other} busy={compare.pending} onClick={() => compare.run()}>Run comparison</Button>
        <div className="mt-2"><ErrorBox error={compare.error} /></div>
      </Card>
      {result && (
        <Card title="Comparison">
          <div className="mb-3 grid gap-3 md:grid-cols-2">
            {(["base", "other"] as const).map((k) => (
              <div key={k} className="rounded-lg bg-slate-50 p-3 text-sm dark:bg-slate-800/50">
                <div className="font-mono font-semibold">v{result[k].version} <Badge value={result[k].passed ? "passed" : "failed"} /></div>
                <div>pass rate {pct(result[k].metrics.pass_rate)} · avg latency {duration(result[k].metrics.avg_latency_ms)} · model calls {result[k].metrics.model_calls}</div>
                <Link className="text-xs text-indigo-600" to={`/evaluations/${result[k].run_id}`}>open run →</Link>
              </div>
            ))}
          </div>
          {result.regressions.length > 0 && <p className="mb-2 text-sm text-rose-600">Regressions: {result.regressions.join(", ")}</p>}
          {result.improvements.length > 0 && <p className="mb-2 text-sm text-emerald-600">Improvements: {result.improvements.join(", ")}</p>}
          <Table head={["Case", `v${result.base.version}`, `v${result.other.version}`]}>
            {Object.entries<any>(result.cases).map(([key, v]) => (
              <tr key={key}><Td className="font-mono text-xs">{key}</Td>
                <Td><Badge value={v.base?.passed ? "passed" : v.base?.status === "pending_review" ? "pending_review" : "failed"} /></Td>
                <Td><Badge value={v.other?.passed ? "passed" : v.other?.status === "pending_review" ? "pending_review" : "failed"} /></Td></tr>
            ))}
          </Table>
        </Card>
      )}
    </div>
  );
}

function AgentVersionPickerOther({ agentId, version, setVersion }: { agentId: string; version: string; setVersion: (v: string) => void }) {
  const { data: versions } = useApi<any[]>(agentId ? `/api/v1/agents/${agentId}/versions` : null);
  return (
    <Field label="Other version">
      <select className={inputClass} value={version} onChange={(e) => setVersion(e.target.value)} disabled={!agentId}>
        <option value="">choose…</option>
        {versions?.filter((v) => v.executable).map((v) => <option key={v.id} value={v.version}>{v.version} ({v.status})</option>)}
      </select>
    </Field>
  );
}

const SUITE_EXAMPLE = JSON.stringify({
  name: "my-regression-suite",
  acceptance: { min_pass_rate: 1.0 },
  cases: [{ id: "case-1", name: "Example case", kind: "functional", input: { task: "…" }, expected: { status: "completed", output_schema_valid: true } }],
}, null, 2);

function Suites() {
  const { data, reload } = useApi<any[]>("/api/v1/evaluation-suites");
  const [text, setText] = useState(SUITE_EXAMPLE);
  const create = useAction(async () => {
    await post("/api/v1/evaluation-suites", JSON.parse(text));
    reload();
  });
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Suites">
        {!data?.length ? <Empty>No suites yet. Suites are created automatically from agent specifications on first run.</Empty> : (
          <Table head={["Suite", "Version", "Cases hash", "Created"]}>
            {data.map((s) => <tr key={s.id}><Td>{s.name}</Td><Td className="font-mono text-xs">{s.version}</Td><Td className="font-mono text-xs">{s.content_hash.slice(0, 10)}</Td><Td className="text-xs">{timeAgo(s.created_at)}</Td></tr>)}
          </Table>
        )}
      </Card>
      <Card title="Create a suite">
        <textarea aria-label="Suite JSON" className={`${inputClass} h-72 font-mono text-xs`} value={text} onChange={(e) => setText(e.target.value)} />
        <Button className="mt-2" variant="primary" busy={create.pending} onClick={() => create.run()}>Create suite</Button>
        <div className="mt-2"><ErrorBox error={create.error} /></div>
      </Card>
    </div>
  );
}
