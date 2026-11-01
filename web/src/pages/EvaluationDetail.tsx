import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorBox, JsonView, PageHeader, Spinner, Stat, Table, Td } from "../components/ui";
import { post } from "../lib/api";
import { dateTime, duration, pct, shortId } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function EvaluationDetail() {
  const { id } = useParams();
  const { data: run, error, reload } = useApi<any>(`/api/v1/evaluations/${id}`, { interval: 1500, poll: (d) => !d || d.status === "running" });
  const [open, setOpen] = useState<string | null>(null);
  const { can } = useSession();
  const review = useAction(async (resultId: string, passed: boolean) => {
    await post(`/api/v1/evaluations/${id}/results/${resultId}/review`, { passed, notes: passed ? "accepted by reviewer" : "rejected by reviewer" });
    reload();
  });
  if (error) return <ErrorBox error={error} />;
  if (!run) return <Spinner />;
  const m = run.metrics ?? {};
  const s = run.summary ?? {};
  return (
    <div className="space-y-4">
      <PageHeader
        title={<span className="flex items-center gap-3">Evaluation <span className="font-mono">{shortId(run.id)}</span>
          <Badge value={run.status === "completed" ? (run.passed ? "passed" : "failed") : run.status} /></span>}
        subtitle={<><Link className="text-indigo-600" to={`/agents/${run.agent_id}`}>agent version {run.agent_version}</Link> · suite {run.suite?.name} v{run.suite?.version} · started {dateTime(run.started_at)}</>}
      />
      {run.status === "running" && <Spinner label="Running cases through the runtime…" />}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-6">
        <Stat label="Pass rate" value={pct(m.pass_rate)} tone={run.passed ? "good" : run.status === "completed" ? "bad" : undefined} hint={`${s.passed ?? 0}/${s.graded ?? 0} graded`} />
        <Stat label="Task completion" value={pct(m.task_completion_rate)} />
        <Stat label="Schema compliance" value={pct(m.output_schema_compliance)} />
        <Stat label="Tool selection" value={pct(m.correct_tool_selection)} />
        <Stat label="Unauthorized rejected" value={pct(m.unauthorized_action_rejection)} />
        <Stat label="Groundedness" value={pct(m.groundedness)} />
        <Stat label="Avg latency" value={duration(m.avg_latency_ms)} hint={`p95 ${duration(m.p95_latency_ms)}`} />
        <Stat label="Model calls" value={m.model_calls ?? 0} hint={`${m.input_tokens ?? 0} / ${m.output_tokens ?? 0} tokens`} />
        <Stat label="Tool calls" value={m.tool_calls ?? 0} />
        <Stat label="Retries" value={m.retries ?? 0} />
        <Stat label="Failure rate" value={pct(m.failure_rate)} />
        <Stat label="Cost" value={`$${(m.cost_usd ?? 0).toFixed(4)}`} />
      </div>
      {s.acceptance_checks && (
        <Card title="Acceptance criteria">
          <div className="flex flex-wrap gap-4 text-sm">
            {Object.entries<boolean>(s.acceptance_checks).map(([k, v]) => <span key={k}>{k.replaceAll("_", " ")} <Badge value={v ? "passed" : "failed"} /></span>)}
            {s.pending_review?.length > 0 && <span>pending human review: {s.pending_review.join(", ")}</span>}
          </div>
        </Card>
      )}
      <ErrorBox error={review.error} />
      <Card title="Cases">
        {!run.results?.length ? <Empty>No results yet.</Empty> : (
          <Table head={["Case", "Kind", "Result", "Observed status", "Failed checks", "Duration", ""]}>
            {run.results.map((r: any) => {
              const failed = r.checks.filter((c: any) => !c.passed);
              const pending = r.observed?.status === "pending_review";
              return [
                <tr key={r.id}>
                  <Td><div className="font-mono text-xs">{r.case_key}</div><div className="text-xs text-slate-500">{r.case_name}</div></Td>
                  <Td className="text-xs">{r.kind}</Td>
                  <Td><Badge value={pending ? "pending_review" : r.passed ? "passed" : "failed"} /></Td>
                  <Td><Badge value={r.observed?.status} /></Td>
                  <Td className="text-xs text-rose-600">{failed.map((c: any) => c.name).join(", ")}</Td>
                  <Td>{duration(r.duration_ms)}</Td>
                  <Td>
                    <div className="flex gap-1">
                      <Button variant="ghost" onClick={() => setOpen(open === r.id ? null : r.id)}>{open === r.id ? "Hide" : "Inspect"}</Button>
                      {pending && can("evaluations:write") && <>
                        <Button variant="ghost" className="text-emerald-600" onClick={() => review.run(r.id, true)}>Pass</Button>
                        <Button variant="ghost" className="text-rose-600" onClick={() => review.run(r.id, false)}>Fail</Button>
                      </>}
                    </div>
                  </Td>
                </tr>,
                open === r.id && (
                  <tr key={`${r.id}-detail`}><Td colSpan={7}>
                    <div className="grid gap-3 md:grid-cols-3">
                      <div><div className="mb-1 text-xs text-slate-500">Input</div><JsonView value={r.input} maxHeight="14rem" /></div>
                      <div><div className="mb-1 text-xs text-slate-500">Expected</div><JsonView value={r.expected} maxHeight="14rem" /></div>
                      <div><div className="mb-1 text-xs text-slate-500">Observed</div><JsonView value={r.observed} maxHeight="14rem" /></div>
                    </div>
                    <div className="mt-2"><div className="mb-1 text-xs text-slate-500">Checks</div><JsonView value={r.checks} maxHeight="12rem" /></div>
                    {r.execution_id && <Link className="mt-2 inline-block text-sm text-indigo-600" to={`/executions/${r.execution_id}`}>Open execution trace →</Link>}
                  </Td></tr>
                ),
              ];
            })}
          </Table>
        )}
      </Card>
    </div>
  );
}
