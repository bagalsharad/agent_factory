import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import WorkflowGraph from "../components/WorkflowGraph";
import { Badge, Button, Card, cx, Empty, ErrorBox, Field, inputClass, JsonView, PageHeader, Spinner, Table, Td } from "../components/ui";
import { api, ApiError, post, put } from "../lib/api";
import { useApi } from "../lib/hooks";

const STEPS = ["Objective", "Review", "Model", "Tools", "Workflow", "Permissions & budgets", "Save & validate"];
const EXAMPLE = "Create an agent that investigates failed data ingestion pipelines by analyzing logs, identifying probable root causes, and recommending remediation steps.";
const ACTIONS = ["production_restart", "configuration_change", "data_deletion", "external_communication", "deployment", "code_execution"];

type Spec = any;

export default function Designer() {
  const { id } = useParams();
  const navigate = useNavigate();
  const editing = Boolean(id);
  const [step, setStep] = useState(editing ? 1 : 0);
  const [mode, setMode] = useState<"guided" | "advanced">("guided");
  const [objective, setObjective] = useState("");
  const [template, setTemplate] = useState("");
  const [spec, setSpec] = useState<Spec | null>(null);
  const [yaml, setYaml] = useState("");
  const [draftInfo, setDraftInfo] = useState<any>(null);
  const [preview, setPreview] = useState<any>(null);
  const [error, setError] = useState<ApiError>();
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<any>(null);
  const { data: templates } = useApi<any[]>("/api/v1/templates");

  useEffect(() => {
    if (!id) return;
    api(`/api/v1/agents/${id}`).then((agent) => {
      setSpec(agent.spec);
      setYaml(agent.spec_yaml);
    }).catch(setError);
  }, [id]);

  const runPreview = async (payload: { spec?: Spec; spec_yaml?: string }) => {
    try {
      const res = await post("/api/v1/agents/preview", payload);
      setPreview(res);
      setError(undefined);
      return res;
    } catch (err) {
      setError(err as ApiError);
      return null;
    }
  };

  useEffect(() => {
    if (!spec || mode !== "guided") return;
    const timer = window.setTimeout(() => runPreview({ spec }), 400);
    return () => window.clearTimeout(timer);
  }, [spec, mode]);

  const generate = async () => {
    setBusy(true);
    try {
      const draft = await post("/api/v1/agents/draft", { objective, template: template || undefined });
      setDraftInfo(draft);
      setSpec(draft.spec);
      setYaml(draft.yaml);
      setStep(1);
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setBusy(false);
    }
  };

  const update = (mutate: (draft: Spec) => void) => {
    setSpec((prev: Spec) => {
      const next = structuredClone(prev);
      mutate(next);
      return next;
    });
  };

  const switchMode = async (next: "guided" | "advanced") => {
    if (next === "advanced" && spec) {
      const res = await runPreview({ spec });
      if (res) setYaml(res.yaml);
    }
    if (next === "guided" && yaml) {
      const res = await runPreview({ spec_yaml: yaml });
      if (!res) return;
      setSpec(res.spec);
    }
    setMode(next);
  };

  const save = async () => {
    setBusy(true);
    setResult(null);
    try {
      let currentSpec = spec;
      if (mode === "advanced") {
        const res = await runPreview({ spec_yaml: yaml });
        if (!res) return;
        currentSpec = res.spec;
      }
      const saved = editing ? (await put(`/api/v1/agents/${id}`, { spec: currentSpec })).agent : await post("/api/v1/agents", { spec: currentSpec });
      const build = await post(`/api/v1/agents/${saved.id}/build`);
      setResult({ agent: saved, build });
    } catch (err) {
      setError(err as ApiError);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      <PageHeader title={editing ? "Edit agent" : "Agent Designer"}
        subtitle="Describe the agent, review the generated specification, then validate and build it. Generated drafts are never executable until the backend validates and builds them."
        actions={spec && <div className="flex rounded-lg border border-slate-300 p-0.5 dark:border-slate-700">
          {(["guided", "advanced"] as const).map((m) => (
            <button key={m} onClick={() => switchMode(m)} className={cx("rounded-md px-3 py-1 text-sm", mode === m ? "bg-indigo-600 text-white" : "text-slate-600 dark:text-slate-300")}>
              {m === "guided" ? "Guided" : "Advanced YAML"}
            </button>
          ))}
        </div>} />
      <ErrorBox error={error} />
      {mode === "guided" && (
        <ol className="flex flex-wrap gap-2 text-xs">
          {STEPS.map((label, i) => (
            <li key={label}>
              <button disabled={!spec && i > 0} onClick={() => setStep(i)}
                className={cx("rounded-full px-3 py-1 font-medium", i === step ? "bg-indigo-600 text-white" : i < step ? "bg-indigo-100 text-indigo-700 dark:bg-indigo-950 dark:text-indigo-300" : "bg-slate-200 text-slate-600 dark:bg-slate-800 dark:text-slate-400")}>
                {i + 1}. {label}
              </button>
            </li>
          ))}
        </ol>
      )}
      <div className="grid gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          {mode === "advanced" ? (
            <Card title="Specification YAML" actions={<Button onClick={() => runPreview({ spec_yaml: yaml })}>Validate</Button>}>
              <textarea aria-label="Specification YAML" spellCheck={false} className={`${inputClass} h-[36rem] font-mono text-xs`} value={yaml} onChange={(e) => setYaml(e.target.value)} />
            </Card>
          ) : step === 0 ? (
            <Card title="1. Describe the agent">
              <div className="space-y-3">
                <Field label="Objective (natural language)">
                  <textarea aria-label="Objective" className={`${inputClass} h-32`} placeholder={EXAMPLE} value={objective} onChange={(e) => setObjective(e.target.value)} />
                </Field>
                <Field label="Template" hint="Leave on automatic to let the interpreter choose.">
                  <select className={inputClass} value={template} onChange={(e) => setTemplate(e.target.value)}>
                    <option value="">automatic</option>
                    {templates?.map((t) => <option key={t.name} value={t.name}>{t.display_name}</option>)}
                  </select>
                </Field>
                <div className="flex gap-2">
                  <Button variant="primary" busy={busy} disabled={objective.trim().length < 8} onClick={generate}>Generate draft specification</Button>
                  <Button variant="ghost" onClick={() => setObjective(EXAMPLE)}>Use example</Button>
                </div>
              </div>
            </Card>
          ) : !spec ? <Spinner /> : (
            <>
              {step === 1 && <ReviewStep spec={spec} update={update} draft={draftInfo} />}
              {step === 2 && <ModelStep spec={spec} update={update} />}
              {step === 3 && <ToolsStep spec={spec} update={update} />}
              {step === 4 && <WorkflowStep spec={spec} update={update} />}
              {step === 5 && <PermissionsStep spec={spec} update={update} />}
              {step === 6 && (
                <Card title="7. Save, validate and build">
                  <p className="mb-3 text-sm text-slate-600 dark:text-slate-300">
                    Saving creates a <b>draft</b> version{editing ? " (or a new version if the latest is approved or published)" : ""}. The build validates the specification against registered tools, providers and policies and makes it executable for testing.
                  </p>
                  <Button variant="primary" busy={busy} onClick={save}>{editing ? "Save changes & build" : "Save & build"}</Button>
                  {result && <BuildReport result={result} onOpen={() => navigate(`/agents/${result.agent.id}`)} />}
                </Card>
              )}
              <div className="flex justify-between">
                <Button disabled={step === 0} onClick={() => setStep(step - 1)}>Back</Button>
                {step < 6 && <Button variant="primary" onClick={() => setStep(step + 1)}>Next</Button>}
              </div>
            </>
          )}
          {mode === "advanced" && (
            <Card title="Save">
              <Button variant="primary" busy={busy} onClick={save}>{editing ? "Save changes & build" : "Save & build"}</Button>
              {result && <BuildReport result={result} onOpen={() => navigate(`/agents/${result.agent.id}`)} />}
            </Card>
          )}
        </div>
        <div className="space-y-4">
          <ValidationPanel preview={preview} />
          {preview?.workflow && <Card title="Workflow preview"><WorkflowGraph graph={preview.workflow} /></Card>}
        </div>
      </div>
    </div>
  );
}

function ValidationPanel({ preview }: { preview: any }) {
  if (!preview) return <Card title="Validation"><Empty>The specification is validated as you edit.</Empty></Card>;
  const v = preview.validation;
  return (
    <Card title="Validation" actions={<Badge value={v.valid ? "passed" : "failed"} />}>
      {!v.errors.length && !v.warnings.length && <p className="text-sm text-emerald-600">No problems found.</p>}
      <ul className="space-y-2 text-sm">
        {v.errors.map((e: any, i: number) => <li key={`e${i}`} className="text-rose-700 dark:text-rose-300"><code className="text-xs">{e.path}</code><br />{e.message}</li>)}
        {v.warnings.map((w: any, i: number) => <li key={`w${i}`} className="text-amber-700 dark:text-amber-300"><code className="text-xs">{w.path}</code><br />{w.message}</li>)}
      </ul>
    </Card>
  );
}

function BuildReport({ result, onOpen }: { result: any; onOpen: () => void }) {
  const build = result.build;
  return (
    <div className="mt-4 space-y-3">
      <div className="flex items-center gap-2 text-sm">Build <Badge value={build.success ? "passed" : "failed"} /> → status <Badge value={build.status} /></div>
      <Table head={["Stage", "Result", "Detail"]}>
        {build.stages.map((s: any) => (
          <tr key={s.name}><Td className="font-mono text-xs">{s.name}</Td><Td><Badge value={s.status} /></Td><Td className="text-xs">{s.detail}</Td></tr>
        ))}
      </Table>
      {build.errors.map((e: any, i: number) => <div key={i} className="text-sm text-rose-600">{e.path ?? e.stage}: {e.message}</div>)}
      {build.warnings.map((w: any, i: number) => <div key={i} className="text-sm text-amber-600">{w.message}</div>)}
      <Button variant="primary" onClick={onOpen}>Open agent</Button>
    </div>
  );
}

type StepProps = { spec: Spec; update: (fn: (s: Spec) => void) => void };

function ReviewStep({ spec, update, draft }: StepProps & { draft: any }) {
  return (
    <Card title="2. Review the generated configuration">
      {draft && (
        <div className="mb-4 rounded-lg bg-indigo-50 p-3 text-sm dark:bg-indigo-950/50">
          <div>Template <b>{draft.template}</b> · confidence {Math.round(draft.confidence * 100)}%{draft.model_assisted && " · model-assisted"}</div>
          <ul className="mt-1 list-disc pl-5 text-xs">{[...draft.rationale, ...draft.assumptions].map((r: string, i: number) => <li key={i}>{r}</li>)}</ul>
        </div>
      )}
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Name (slug)"><input className={inputClass} value={spec.metadata.name} onChange={(e) => update((s) => { s.metadata.name = e.target.value; })} /></Field>
        <Field label="Display name"><input className={inputClass} value={spec.metadata.display_name ?? ""} onChange={(e) => update((s) => { s.metadata.display_name = e.target.value; })} /></Field>
      </div>
      <div className="mt-3 space-y-3">
        <Field label="Description"><textarea className={`${inputClass} h-16`} value={spec.metadata.description ?? ""} onChange={(e) => update((s) => { s.metadata.description = e.target.value; })} /></Field>
        <Field label="Objective"><textarea className={`${inputClass} h-20`} value={spec.spec.objective} onChange={(e) => update((s) => { s.spec.objective = e.target.value; })} /></Field>
        <Field label="Instructions (system prompt)"><textarea className={`${inputClass} h-40 font-mono text-xs`} value={spec.spec.instructions ?? ""} onChange={(e) => update((s) => { s.spec.instructions = e.target.value; })} /></Field>
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Input schema"><JsonView value={spec.spec.inputs} maxHeight="12rem" /></Field>
          <Field label="Output schema"><JsonView value={spec.spec.outputs} maxHeight="12rem" /></Field>
        </div>
        <p className="text-xs text-slate-500">Edit schemas, workflow steps and evaluation cases in the Advanced YAML view.</p>
      </div>
    </Card>
  );
}

function ModelStep({ spec, update }: StepProps) {
  const { data: providers } = useApi<any[]>("/api/v1/models/providers");
  const provider = spec.spec.model?.provider ?? "mock";
  const { data: models } = useApi<any>(`/api/v1/models/available?provider=${provider}`);
  const caps = ["chat", "tools", "structured_output", "streaming", "vision"];
  return (
    <Card title="3. Select a model">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Provider" hint="Cloud providers stay disabled unless explicitly enabled by an operator.">
          <select className={inputClass} value={provider} onChange={(e) => update((s) => { s.spec.model = { ...s.spec.model, provider: e.target.value }; })}>
            {providers?.map((p) => <option key={p.name} value={p.name} disabled={!p.enabled}>{p.name} {p.local ? "(local)" : "(cloud)"} {p.enabled ? `· ${p.status}` : "· disabled"}</option>)}
          </select>
        </Field>
        <Field label="Model" hint={models?.errors?.[provider]}>
          <input list="model-options" className={inputClass} value={spec.spec.model?.model ?? ""} onChange={(e) => update((s) => { s.spec.model = { ...s.spec.model, model: e.target.value }; })} />
          <datalist id="model-options">{models?.models?.map((m: any) => <option key={m.name} value={m.name} />)}</datalist>
        </Field>
        <Field label="Temperature"><input type="number" min={0} max={2} step={0.1} className={inputClass} value={spec.spec.model?.temperature ?? 0}
          onChange={(e) => update((s) => { s.spec.model = { ...s.spec.model, temperature: Number(e.target.value) }; })} /></Field>
        <Field label="Max output tokens"><input type="number" min={16} className={inputClass} value={spec.spec.model?.max_output_tokens ?? ""}
          onChange={(e) => update((s) => { s.spec.model = { ...s.spec.model, max_output_tokens: e.target.value ? Number(e.target.value) : undefined }; })} /></Field>
      </div>
      <fieldset className="mt-4">
        <legend className="text-xs font-medium text-slate-600 dark:text-slate-300">Required capabilities</legend>
        <div className="mt-1 flex flex-wrap gap-3 text-sm">
          {caps.map((c) => (
            <label key={c} className="flex items-center gap-1.5">
              <input type="checkbox" checked={(spec.spec.model?.required_capabilities ?? ["chat"]).includes(c)}
                onChange={(e) => update((s) => {
                  const current = new Set<string>(s.spec.model?.required_capabilities ?? ["chat"]);
                  if (e.target.checked) current.add(c); else current.delete(c);
                  s.spec.model = { ...s.spec.model, required_capabilities: [...current] };
                })} />{c}
            </label>
          ))}
        </div>
      </fieldset>
    </Card>
  );
}

function ToolsStep({ spec, update }: StepProps) {
  const { data: tools } = useApi<any[]>("/api/v1/tools");
  const selected = new Set<string>((spec.spec.tools ?? []).map((t: any) => (typeof t === "string" ? t : t.name)));
  return (
    <Card title="4. Add approved tools">
      <p className="mb-3 text-xs text-slate-500">Tools are registered platform code. Write and network tools also require explicit policies (next steps) and are gated by human approval.</p>
      <Table head={["", "Tool", "Risk", "Side effects", "Approval class"]}>
        {tools?.map((t) => (
          <tr key={t.name} className={cx(!t.enabled && "opacity-50")}>
            <Td><input type="checkbox" aria-label={`Select ${t.name}`} disabled={!t.enabled} checked={selected.has(t.name)}
              onChange={(e) => update((s) => {
                const names = new Set<string>((s.spec.tools ?? []).map((x: any) => (typeof x === "string" ? x : x.name)));
                if (e.target.checked) names.add(t.name); else names.delete(t.name);
                s.spec.tools = [...names].map((name) => ({ name }));
              })} /></Td>
            <Td><div className="font-mono text-xs">{t.name}</div><div className="text-xs text-slate-500">{t.description}</div></Td>
            <Td><Badge value={t.risk} /></Td>
            <Td className="text-xs">{t.side_effects}{t.network ? " · network" : ""}{t.requires_sandbox ? " · sandbox" : ""}</Td>
            <Td className="text-xs">{t.action_class ?? "—"}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function WorkflowStep({ spec, update }: StepProps) {
  const { data: agents } = useApi<any>("/api/v1/agents?limit=200");
  const wf = spec.spec.workflow ?? { type: "single", steps: [] };
  const knowledge = spec.spec.memory?.knowledge ?? {};
  return (
    <Card title="5. Define workflow behaviour">
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Pattern">
          <select className={inputClass} value={wf.type} onChange={(e) => update((s) => { s.spec.workflow = { ...wf, type: e.target.value }; })}>
            <option value="single">single agent</option>
            <option value="graph">graph (deterministic steps)</option>
            <option value="sequential">sequential multi-agent</option>
            <option value="supervisor">supervisor + specialists</option>
          </select>
        </Field>
        <Field label="Max delegations"><input type="number" min={1} max={20} className={inputClass} value={wf.max_delegations ?? 6}
          onChange={(e) => update((s) => { s.spec.workflow = { ...wf, max_delegations: Number(e.target.value) }; })} /></Field>
      </div>
      <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Steps</h3>
      <Table head={["Id", "Kind", "Detail", "Final"]} empty={!wf.steps?.length}>
        {wf.steps?.map((st: any) => (
          <tr key={st.id}><Td className="font-mono text-xs">{st.id}</Td><Td><Badge value={st.kind} /></Td>
            <Td className="text-xs">{st.tool ?? st.agent ?? st.action ?? st.prompt?.slice(0, 80) ?? ""}</Td><Td>{st.final ? "✓" : ""}</Td></tr>
        ))}
      </Table>
      {wf.type === "supervisor" && (
        <div className="mt-4">
          <h3 className="text-xs font-semibold uppercase text-slate-500">Specialists</h3>
          <ul className="mt-2 space-y-2">
            {(wf.specialists ?? []).map((sp: any, i: number) => (
              <li key={i} className="flex gap-2">
                <input aria-label="Specialist id" className={inputClass} value={sp.id} onChange={(e) => update((s) => { s.spec.workflow.specialists[i].id = e.target.value; })} />
                <select aria-label="Specialist agent" className={inputClass} value={sp.agent} onChange={(e) => update((s) => { s.spec.workflow.specialists[i].agent = e.target.value; })}>
                  <option value="">choose agent…</option>
                  {agents?.items?.map((a: any) => <option key={a.id} value={a.name}>{a.name}</option>)}
                </select>
                <Button variant="ghost" onClick={() => update((s) => { s.spec.workflow.specialists.splice(i, 1); })}>Remove</Button>
              </li>
            ))}
          </ul>
          <Button className="mt-2" onClick={() => update((s) => { s.spec.workflow.specialists = [...(s.spec.workflow.specialists ?? []), { id: `specialist_${(s.spec.workflow.specialists ?? []).length + 1}`, agent: "" }]; })}>Add specialist</Button>
        </div>
      )}
      <h3 className="mt-4 text-xs font-semibold uppercase text-slate-500">Knowledge (retrieval)</h3>
      <div className="mt-2 grid gap-3 md:grid-cols-2">
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(knowledge.enabled)}
          onChange={(e) => update((s) => { s.spec.memory = { ...(s.spec.memory ?? {}), enabled: undefined, knowledge: { ...knowledge, enabled: e.target.checked } }; })} />Enable workspace knowledge</label>
        <Field label="Collections (comma separated)"><input className={inputClass} value={(knowledge.collections ?? []).join(", ")}
          onChange={(e) => update((s) => { s.spec.memory = { ...(s.spec.memory ?? {}), knowledge: { ...knowledge, collections: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) } }; })} /></Field>
      </div>
    </Card>
  );
}

function PermissionsStep({ spec, update }: StepProps) {
  const policies = spec.spec.policies ?? {};
  const approval = spec.spec.human_approval ?? {};
  const limits = spec.spec.execution_limits ?? {};
  const limitFields = useMemo(() => ["max_steps", "max_model_calls", "max_tool_calls", "timeout_seconds", "max_retries", "max_context_tokens", "max_output_tokens", "max_subagents"], []);
  return (
    <Card title="6. Configure permissions and budgets">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(policies.allow_write_operations)}
            onChange={(e) => update((s) => { s.spec.policies = { ...policies, allow_write_operations: e.target.checked }; })} />Allow write operations (always approval-gated)</label>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={Boolean(policies.external_network)}
            onChange={(e) => update((s) => { s.spec.policies = { ...policies, external_network: e.target.checked }; })} />Allow external network (allowlisted hosts only)</label>
          <Field label="Allowed network hosts" hint="Must also be in the platform allowlist (AF_NETWORK_ALLOWLIST).">
            <input className={inputClass} value={(policies.allowed_network_hosts ?? []).join(", ")}
              onChange={(e) => update((s) => { s.spec.policies = { ...policies, allowed_network_hosts: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) }; })} />
          </Field>
          <fieldset>
            <legend className="text-xs font-medium text-slate-600 dark:text-slate-300">Require human approval for</legend>
            <div className="mt-1 grid grid-cols-2 gap-1 text-sm">
              {ACTIONS.map((a) => (
                <label key={a} className="flex items-center gap-1.5"><input type="checkbox" checked={(policies.require_approval_for ?? []).includes(a)}
                  onChange={(e) => update((s) => {
                    const set = new Set<string>(policies.require_approval_for ?? []);
                    if (e.target.checked) set.add(a); else set.delete(a);
                    s.spec.policies = { ...policies, require_approval_for: [...set] };
                  })} />{a.replaceAll("_", " ")}</label>
              ))}
            </div>
          </fieldset>
          <fieldset>
            <legend className="text-xs font-medium text-slate-600 dark:text-slate-300">Approval required at risk levels</legend>
            <div className="mt-1 flex gap-3 text-sm">
              {["low", "medium", "high", "critical"].map((r) => (
                <label key={r} className="flex items-center gap-1.5"><input type="checkbox" checked={(approval.required_for_risk ?? ["high", "critical"]).includes(r)}
                  onChange={(e) => update((s) => {
                    const set = new Set<string>(approval.required_for_risk ?? ["high", "critical"]);
                    if (e.target.checked) set.add(r); else set.delete(r);
                    s.spec.human_approval = { ...approval, required_for_risk: [...set] };
                  })} />{r}</label>
              ))}
            </div>
          </fieldset>
          <Field label="Approver roles"><input className={inputClass} value={(approval.approver_roles ?? ["admin", "operator"]).join(", ")}
            onChange={(e) => update((s) => { s.spec.human_approval = { ...approval, approver_roles: e.target.value.split(",").map((x) => x.trim()).filter(Boolean) }; })} /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          {limitFields.map((f) => (
            <Field key={f} label={f.replaceAll("_", " ")}>
              <input type="number" min={0} className={inputClass} value={limits[f] ?? ""}
                onChange={(e) => update((s) => { s.spec.execution_limits = { ...limits, [f]: e.target.value === "" ? undefined : Number(e.target.value) }; })} />
            </Field>
          ))}
          <Field label="max cost usd"><input type="number" min={0} step={0.01} className={inputClass} value={limits.max_cost_usd ?? ""}
            onChange={(e) => update((s) => { s.spec.execution_limits = { ...limits, max_cost_usd: e.target.value === "" ? undefined : Number(e.target.value) }; })} /></Field>
        </div>
      </div>
      <p className="mt-4 text-xs text-slate-500">These settings are enforced by the platform's policy engine, never by the model. <Link className="text-indigo-600" to="/catalog">Review tool risk classifications</Link>.</p>
    </Card>
  );
}
