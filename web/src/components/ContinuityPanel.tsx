import { useState } from "react";
import { post } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";
import { icons } from "./icons";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputClass, Spinner, cx } from "./ui";

const DIMENSIONS: [string, string][] = [
  ["goal_preservation", "Goals"],
  ["constraint_preservation", "Constraints"],
  ["decision_consistency", "Decisions"],
  ["action_integrity", "Actions"],
  ["state_consistency", "State"],
];
const DECISION_TONE: Record<string, string> = {
  resume: "passed", revalidate_resume: "passed", replay: "running", replan: "running", escalate: "needs_review",
};

/** A labelled 0–1 meter: the fill and its track share one hue (blue), the value is printed as text. */
function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <div className="flex justify-between text-xs"><span className="text-slate-500">{label}</span><span className="tabular-nums">{value.toFixed(2)}</span></div>
      <div className="mt-1 h-1.5 rounded-full bg-[#cde2fb] dark:bg-[#184f95]" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={1} aria-valuenow={value}>
        <div className="h-1.5 rounded-full bg-[#2a78d6] dark:bg-[#3987e5]" style={{ width: `${Math.round(value * 100)}%` }} />
      </div>
    </div>
  );
}

/** Agentic Continuity Engineering: intent ledger, execution state graph and the latest continuity assessment. */
export function ContinuityPanel({ executionId, status, onChange }: { executionId: string; status: string; onChange: () => void }) {
  const { data, error, reload } = useApi<any>(`/api/v1/executions/${executionId}/continuity`, { interval: 5000 });
  const { can } = useSession();
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<any>(null);
  const [amendInput, setAmendInput] = useState<string | null>(null);
  const [amendReason, setAmendReason] = useState("");
  const [amended, setAmended] = useState<any>(null);
  const assess = useAction(async () => setPreview(await post(`/api/v1/executions/${executionId}/continuity/assess`)));
  const decide = useAction(async (decision: string, fromStep?: string | null) => {
    await post(`/api/v1/executions/${executionId}/continuity/decision`, { decision, from_step: fromStep ?? null, note });
    setNote(""); reload(); onChange();
  });
  const resolve = useAction(async (invocationId: string, performed: boolean) => {
    await post(`/api/v1/executions/${executionId}/tool-invocations/${invocationId}/resolve`, { performed, note, resume: true });
    setNote(""); reload(); onChange();
  });
  const amend = useAction(async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(amendInput ?? "{}"); } catch { throw new Error("The new input must be valid JSON"); }
    const result = await post<any>(`/api/v1/executions/${executionId}/intent`, { input: parsed, reason: amendReason });
    setAmended(result); setAmendReason(""); reload(); onChange();
  });
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  const latest = preview ?? data.assessments[0];
  const intent = data.intent[data.intent.length - 1];
  const held = status === "needs_review";
  const stopped = ["failed", "dead_lettered", "needs_review"].includes(status);
  const inDoubt: string[] = (latest?.findings ?? [])
    .filter((f: any) => f.check === "action_integrity" && f.status === "fail" && f.severity === "major")
    .flatMap((f: any) => f.evidence ?? []);
  return (
    <Card icon="layers" title="Continuity" subtitle="Agentic Continuity Engineering: intent, semantic checkpoints and recovery decisions"
      actions={<Button icon="search" busy={assess.pending} onClick={() => assess.run()}>Assess now</Button>}>
      <ErrorBox error={assess.error ?? decide.error ?? resolve.error ?? (amend.error as any)} />
      <div className="grid gap-5 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-1">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-400">Intent ledger</div>
          {intent ? (
            <div className="space-y-1 text-sm">
              <div>Version <b>{intent.version}</b>{data.intent.length > 1 && <span className="text-slate-500"> · amended {data.intent.length - 1}×</span>}</div>
              <div className="text-slate-500">{intent.intent.goals.objective}</div>
              <div className="font-mono text-xs text-slate-500">input {JSON.stringify(intent.intent.goals.input).slice(0, 120)}</div>
              {intent.version > 1 && <div className="text-xs text-amber-700">Reason: {intent.reason}</div>}
            </div>
          ) : <Empty>No intent recorded.</Empty>}
          <div className="pt-2 text-xs font-semibold uppercase tracking-wider text-slate-400">Execution state graph</div>
          <ol className="space-y-1.5">
            {data.graph.map((node: any) => {
              const last = node.records[node.records.length - 1];
              const replays = node.records.filter((r: any) => r.validity === "invalidated").length;
              return (
                <li key={node.step_id} className="flex items-center gap-2 text-sm">
                  <span className={cx("h-2 w-2 rounded-full", !last ? "bg-slate-300 dark:bg-slate-600" : last.validity === "invalidated" ? "bg-amber-500" : "bg-emerald-500")} />
                  <span className="font-mono text-xs">{node.step_id}</span>
                  <span className="text-xs text-slate-400">{node.kind}</span>
                  {last?.side_effects?.length > 0 && <span className="rounded bg-rose-50 px-1 text-[10px] text-rose-700 dark:bg-rose-500/10 dark:text-rose-300">side effect</span>}
                  {replays > 0 && <span className="text-[10px] text-amber-700">replayed {replays}×</span>}
                  {node.depends_on.steps.length > 0 && <span className="ml-auto truncate text-[10px] text-slate-400">← {node.depends_on.steps.join(", ")}</span>}
                </li>
              );
            })}
          </ol>
        </div>
        <div className="space-y-3 lg:col-span-2">
          {!latest ? <Empty>No assessment yet. Assessments run automatically before every recovery or resume.</Empty> : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                <Badge value={DECISION_TONE[latest.decision] ?? "pending"} title={latest.decision} />
                <span className="text-sm font-semibold">{latest.decision.replaceAll("_", " ")}{latest.from_step && <> from <span className="font-mono">{latest.from_step}</span></>}</span>
                <span className="text-xs text-slate-500">{latest.status === "preview" ? "preview (not recorded)" : `${latest.trigger} · ${timeAgo(latest.created_at)}`}</span>
              </div>
              <p className="text-sm text-slate-600 dark:text-slate-300">{latest.rationale}</p>
              <div className="grid gap-4 sm:grid-cols-[auto_1fr]">
                <div className="rounded-xl bg-slate-50 px-4 py-3 text-center dark:bg-white/[0.03]">
                  <div className="text-xs text-slate-500">Semantic Continuity Score</div>
                  <div className="text-3xl font-semibold">{latest.scores.scs.toFixed(2)}</div>
                  <div className="mt-1 flex items-center justify-center gap-1 text-xs">
                    {latest.scores.hard_invariants ? <><icons.check className="h-3.5 w-3.5 text-emerald-600" /> invariants hold</>
                      : <><icons.x className="h-3.5 w-3.5 text-rose-600" /> invariant violated</>}
                  </div>
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {DIMENSIONS.map(([key, label]) => <Meter key={key} label={label} value={latest.scores.dimensions[key]} />)}
                </div>
              </div>
              {latest.changes.length > 0 && (
                <div>
                  <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-400">What changed</div>
                  <ul className="space-y-1 text-sm">{latest.changes.map((c: any, i: number) => (
                    <li key={i} className="flex gap-2"><span className="font-mono text-xs text-slate-400">{c.type}</span>{c.step && <span className="font-mono text-xs">{c.step}</span>}<span>{c.detail}</span></li>
                  ))}</ul>
                </div>
              )}
              <div>
                <div className="mb-1 text-xs font-semibold uppercase tracking-wider text-slate-400">Validator findings</div>
                <ul className="space-y-1 text-sm">{latest.findings.map((f: any, i: number) => (
                  <li key={i} className="flex items-start gap-2">
                    {f.status === "pass" ? <icons.check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" /> : <icons.alert className={cx("mt-0.5 h-3.5 w-3.5 shrink-0", f.severity === "critical" ? "text-rose-600" : "text-amber-600")} />}
                    <span className="font-mono text-xs text-slate-400">{f.check}</span><span>{f.detail}</span>
                  </li>
                ))}</ul>
              </div>
            </>
          )}
          {held && inDoubt.length > 0 && can("executions:recover") && (
            <div className="rounded-xl border border-rose-300 bg-rose-50/60 p-3 dark:border-rose-500/30 dark:bg-rose-500/10">
              <div className="text-sm font-medium">An action was interrupted mid-call and may or may not have happened.</div>
              <p className="mt-1 text-xs text-slate-600 dark:text-slate-300">Check the external system, then say what happened. The execution continues afterwards; an action you confirm as performed is never repeated.</p>
              <Field label="What you checked (recorded in the audit log)"><input className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
              {inDoubt.map((id) => (
                <div key={id} className="mt-2 flex flex-wrap items-center gap-2 text-sm">
                  <span className="font-mono text-xs">{id.slice(0, 8)}</span>
                  <Button disabled={note.length < 5} busy={resolve.pending} onClick={() => resolve.run(id, true)}>It happened — don't repeat it</Button>
                  <Button disabled={note.length < 5} onClick={() => resolve.run(id, false)}>It did not happen — run it again</Button>
                </div>
              ))}
            </div>
          )}
          {stopped && can("executions:recover") && (
            <details className="rounded-xl border border-slate-200 p-3 dark:border-white/10">
              <summary className="cursor-pointer text-sm font-medium">Amend the intent</summary>
              <p className="mt-1 text-xs text-slate-500">A new input changes the goals: the work is re-planned (unless something irreversible already happened, which goes to review). Recorded as a new intent version.</p>
              <Field label="New task input (JSON)">
                <textarea className={inputClass + " font-mono"} rows={3}
                  value={amendInput ?? JSON.stringify(intent?.intent.goals.input ?? {}, null, 2)}
                  onChange={(e) => setAmendInput(e.target.value)} />
              </Field>
              <Field label="Reason"><input className={inputClass} value={amendReason} onChange={(e) => setAmendReason(e.target.value)} /></Field>
              <div className="mt-2 flex items-center gap-3">
                <Button disabled={amendReason.length < 5} busy={amend.pending} onClick={() => amend.run()}>Amend intent</Button>
                {amended && <span className="text-xs text-slate-600 dark:text-slate-300">Version {amended.intent.version} recorded. On recovery ACE will: <b>{amended.preview.decision}</b>{status === "failed" && " — use “Recover from checkpoint” above."}</span>}
              </div>
            </details>
          )}
          {held && can("executions:recover") && (
            <div className="rounded-xl border border-amber-300 bg-amber-50/60 p-3 dark:border-amber-500/30 dark:bg-amber-500/10">
              <div className="mb-2 text-sm font-medium">This execution is held for review. How should it continue?</div>
              <Field label="Reason (recorded in the audit log)"><input className={inputClass} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button disabled={note.length < 5} busy={decide.pending} onClick={() => decide.run("resume")}>Resume as is</Button>
                {latest?.from_step && <Button disabled={note.length < 5} onClick={() => decide.run("replay", latest.from_step)}>Replay from {latest.from_step}</Button>}
                <Button disabled={note.length < 5} onClick={() => decide.run("replan")}>Re-plan</Button>
                <Button variant="danger" disabled={note.length < 5} onClick={() => decide.run("cancel")}>Cancel</Button>
              </div>
              <p className="mt-2 text-xs text-slate-500">Replaying or re-planning redoes the affected steps; committed side effects you choose to redo will run again.</p>
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
