import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import Timeline, { type ExecutionEvent } from "../components/Timeline";
import WorkflowGraph, { statusFromEvents } from "../components/WorkflowGraph";
import { Badge, Button, Card, Empty, ErrorBox, JsonView, PageHeader, Spinner, Stat, Table, Td } from "../components/ui";
import { post } from "../lib/api";
import { ACTIVE_STATUSES, dateTime, duration, shortId } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function ExecutionDetail() {
  const { id } = useParams();
  const isActive = (d: any) => !d || ACTIVE_STATUSES.has(d.status);
  const { data: execution, error, reload } = useApi<any>(`/api/v1/executions/${id}`, { interval: 1500, poll: isActive });
  const { data: events } = useApi<{ items: ExecutionEvent[]; status: string }>(`/api/v1/executions/${id}/events`, {
    interval: 1000, poll: (d) => !d || ACTIVE_STATUSES.has(d.status),
  });
  const [selected, setSelected] = useState<string | null>(null);
  const { can } = useSession();
  const act = useAction(async (path: string) => {
    const result = await post(path);
    reload();
    return result;
  });
  const nodeStatus = useMemo(() => statusFromEvents(events?.items ?? []), [events]);

  if (error) return <ErrorBox error={error} />;
  if (!execution) return <Spinner />;
  const usage = execution.budget_usage ?? {};
  const nodeEvents = (events?.items ?? []).filter((e) => e.node_id === selected || (selected?.endsWith("__tools") && e.node_id === selected));
  const nodeInvocations = execution.tool_invocations.filter((t: any) => selected && (t.step_id === selected || `${t.step_id}__tools` === selected));

  return (
    <div className="space-y-4">
      <PageHeader
        title={<span className="flex items-center gap-3">Execution <span className="font-mono">{shortId(execution.id)}</span><Badge value={execution.status} /></span>}
        subtitle={<>
          <Link className="text-indigo-600" to={`/agents/${execution.agent?.id}`}>{execution.agent?.display_name ?? execution.agent?.name}</Link> v{execution.version}
          {" · "}{execution.mode} · created {dateTime(execution.created_at)}
          {execution.parent_execution_id && <> · child of <Link className="font-mono text-indigo-600" to={`/executions/${execution.parent_execution_id}`}>{shortId(execution.parent_execution_id)}</Link></>}
        </>}
        actions={<>
          {execution.status === "waiting_approval" && <Link to="/approvals"><Button variant="primary">Review approval</Button></Link>}
          {ACTIVE_STATUSES.has(execution.status) && can("executions:cancel") && (
            <Button variant="danger" busy={act.pending} onClick={() => act.run(`/api/v1/executions/${execution.id}/cancel`)}>Cancel</Button>
          )}
          {execution.status === "failed" && can("executions:run") && (
            <Button busy={act.pending} onClick={() => act.run(`/api/v1/executions/${execution.id}/retry`)}>Recover from checkpoint</Button>
          )}
        </>}
      />
      <ErrorBox error={act.error} />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        <Stat label="Duration" value={duration(execution.duration_ms ?? (usage.active_seconds ? usage.active_seconds * 1000 : null))} />
        <Stat label="Steps" value={usage.steps ?? 0} />
        <Stat label="Model calls" value={usage.model_calls ?? 0} hint={`${usage.input_tokens ?? 0} in / ${usage.output_tokens ?? 0} out tokens`} />
        <Stat label="Tool calls" value={usage.tool_calls ?? 0} hint={`${usage.retries ?? 0} retries`} />
        <Stat label="Cost" value={`$${(usage.cost_usd ?? 0).toFixed(4)}`} hint={`${usage.delegations ?? 0} delegations`} />
      </div>
      {execution.error && (
        <Card title="Error"><div className="text-sm text-rose-700 dark:text-rose-300"><b>{execution.error.code}</b>: {execution.error.message}</div>
          {execution.error.details && <JsonView value={execution.error.details} maxHeight="12rem" />}</Card>
      )}
      <div className="grid gap-4 lg:grid-cols-5">
        <Card title="Workflow" className="lg:col-span-3">
          {execution.graph ? <WorkflowGraph graph={execution.graph} status={nodeStatus} selected={selected} onSelect={setSelected} /> : <Empty>No graph recorded.</Empty>}
        </Card>
        <Card title={selected ? `Node · ${selected}` : "Node inspector"} className="lg:col-span-2">
          {!selected ? <Empty>Select a node to inspect inputs, outputs, duration, tool calls, errors and approvals.</Empty> : (
            <div className="space-y-3 text-sm">
              <div>Status: <Badge value={nodeStatus[selected] ?? "pending"} /></div>
              {nodeEvents.filter((e) => e.type === "step.completed" || e.type === "step.failed").map((e) => (
                <div key={e.seq}>
                  <div className="text-xs text-slate-500">{e.type} · {duration(e.duration_ms)}</div>
                  <JsonView value={e.payload.output ?? e.payload.error ?? e.payload} maxHeight="14rem" />
                </div>
              ))}
              {nodeInvocations.map((t: any) => (
                <div key={t.id} className="rounded-lg border border-slate-200 p-2 dark:border-slate-800">
                  <div className="flex items-center gap-2"><span className="font-mono text-xs">{t.tool_name}</span><Badge value={t.status} /><span className="text-xs text-slate-500">{duration(t.duration_ms)}</span></div>
                  <div className="mt-1 text-xs text-slate-500">arguments</div><JsonView value={t.arguments} maxHeight="8rem" />
                  {t.decision?.reasons && <div className="mt-1 text-xs">policy: {t.decision.effect} — {t.decision.reasons.join("; ")}</div>}
                </div>
              ))}
              {nodeEvents.filter((e) => e.type.startsWith("model.")).map((e) => (
                <div key={e.seq} className="text-xs">{e.type}: {e.payload.provider}/{e.payload.model} · {duration(e.duration_ms)}</div>
              ))}
            </div>
          )}
        </Card>
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Output">{execution.output !== null && execution.output !== undefined ? <JsonView value={execution.output} /> : <Empty>{ACTIVE_STATUSES.has(execution.status) ? "Running…" : "No output."}</Empty>}</Card>
        <Card title="Input & configuration">
          <JsonView value={execution.input} maxHeight="10rem" />
          <div className="mt-3 text-xs text-slate-500">Configuration snapshot (provenance)</div>
          <JsonView value={execution.config_snapshot} maxHeight="14rem" />
        </Card>
      </div>
      <Card title="Tool invocations">
        <Table head={["Step", "Tool", "Status", "Policy", "Duration", "Approval"]} empty={!execution.tool_invocations.length}>
          {execution.tool_invocations.map((t: any) => (
            <tr key={t.id}>
              <Td className="font-mono text-xs">{t.step_id}</Td>
              <Td className="font-mono text-xs">{t.tool_name}@{t.tool_version}</Td>
              <Td><Badge value={t.status} /></Td>
              <Td className="max-w-sm text-xs">{t.decision?.effect && <Badge value={t.decision.effect} />} {t.decision?.reasons?.join("; ")}</Td>
              <Td>{duration(t.duration_ms)}</Td>
              <Td className="font-mono text-xs">{t.approval_id ? shortId(t.approval_id) : "—"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      {(execution.children?.length > 0 || execution.approvals?.length > 0) && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title="Sub-agent executions">
            <Table head={["Execution", "Status", "Duration"]} empty={!execution.children.length}>
              {execution.children.map((c: any) => (
                <tr key={c.id}><Td><Link className="font-mono text-indigo-600" to={`/executions/${c.id}`}>{shortId(c.id)}</Link></Td><Td><Badge value={c.status} /></Td><Td>{duration(c.duration_ms)}</Td></tr>
              ))}
            </Table>
          </Card>
          <Card title="Approvals">
            <Table head={["Approval", "Action", "Status", "Requested"]} empty={!execution.approvals.length}>
              {execution.approvals.map((a: any) => (
                <tr key={a.id}><Td className="font-mono text-xs">{shortId(a.id)}</Td><Td>{a.action}</Td><Td><Badge value={a.status} /></Td><Td className="text-xs">{dateTime(a.requested_at)}</Td></tr>
              ))}
            </Table>
          </Card>
        </div>
      )}
      <Card title={`Timeline (${events?.items.length ?? 0} events)`}>
        {events ? <Timeline events={events.items} onSelectNode={setSelected} /> : <Spinner />}
      </Card>
    </div>
  );
}
