import { useState } from "react";
import { Link } from "react-router-dom";
import { Badge, Button, Card, Empty, ErrorBox, inputClass, JsonView, PageHeader, Spinner, Tabs } from "../components/ui";
import { post } from "../lib/api";
import { dateTime, shortId, timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function Approvals() {
  const [status, setStatus] = useState("pending");
  const { data, error, loading, reload } = useApi<any>(`/api/v1/approvals?status=${status}&limit=100`, { interval: 3000 });
  const { can } = useSession();
  return (
    <div className="space-y-4">
      <PageHeader title="Approvals" subtitle="Consequential actions pause here until an authorized human decides. The model can never approve its own actions." />
      <Tabs tabs={["pending", "approved", "rejected", "expired", "cancelled"].map((s) => ({ id: s, label: s }))} active={status} onChange={setStatus} />
      <ErrorBox error={error} />
      {loading && !data ? <Spinner /> : !data?.items.length ? <Card><Empty>No {status} approvals.</Empty></Card> : (
        <div className="space-y-3">
          {data.items.map((a: any) => <ApprovalCard key={a.id} approval={a} canDecide={can("approvals:decide")} onDecided={reload} />)}
        </div>
      )}
    </div>
  );
}

function ApprovalCard({ approval, canDecide, onDecided }: { approval: any; canDecide: boolean; onDecided: () => void }) {
  const [comment, setComment] = useState("");
  const decide = useAction(async (decision: "approve" | "reject") => {
    const result = await post(`/api/v1/approvals/${approval.id}/${decision}`, { comment });
    onDecided();
    return result;
  });
  return (
    <Card title={<span className="flex items-center gap-2">{approval.action.replaceAll("_", " ")} <Badge value={approval.risk} /> <Badge value={approval.status} /></span>}
      actions={<Link className="text-sm text-indigo-600" to={`/executions/${approval.execution_id}`}>Execution {shortId(approval.execution_id)} →</Link>}>
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-2 text-sm">
          <div><span className="text-slate-500">Tool:</span> <span className="font-mono">{approval.tool_name ?? "workflow checkpoint"}</span> · step <span className="font-mono">{approval.step_id}</span></div>
          <div><span className="text-slate-500">Rationale:</span> {approval.rationale || "—"}</div>
          <div className="text-xs text-slate-500">Requested {timeAgo(approval.requested_at)} by {approval.requested_by ? shortId(approval.requested_by) : "system"} · expires {dateTime(approval.expires_at)}</div>
          {approval.decided_at && <div className="text-xs text-slate-500">Decided {dateTime(approval.decided_at)} by {approval.decided_by ? shortId(approval.decided_by) : "simulated reviewer"}{approval.decision_comment && ` — "${approval.decision_comment}"`}</div>}
        </div>
        <div className="space-y-2">
          <div className="text-xs text-slate-500">Requested arguments</div>
          <JsonView value={approval.arguments} maxHeight="9rem" />
          {approval.evidence && Object.keys(approval.evidence).length > 0 && <><div className="text-xs text-slate-500">Evidence</div><JsonView value={approval.evidence} maxHeight="9rem" /></>}
        </div>
      </div>
      {approval.status === "pending" && canDecide && (
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <input aria-label="Decision comment" className={`${inputClass} max-w-md`} placeholder="Comment (recorded in the audit log)" value={comment} onChange={(e) => setComment(e.target.value)} />
          <Button variant="primary" busy={decide.pending} onClick={() => decide.run("approve")}>Approve</Button>
          <Button variant="danger" busy={decide.pending} onClick={() => decide.run("reject")}>Reject</Button>
        </div>
      )}
      {approval.status === "pending" && !canDecide && <p className="mt-3 text-xs text-slate-500">Your role cannot decide approvals.</p>}
      <div className="mt-2"><ErrorBox error={decide.error} /></div>
    </Card>
  );
}
