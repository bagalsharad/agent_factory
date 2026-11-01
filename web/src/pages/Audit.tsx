import { useState } from "react";
import { Badge, Button, Card, ErrorBox, inputClass, PageHeader, Spinner, Table, Td } from "../components/ui";
import { api } from "../lib/api";
import { dateTime, shortId } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";

export default function Audit() {
  const [action, setAction] = useState("");
  const { data, error, loading } = useApi<any>(`/api/v1/audit?limit=200${action ? `&action=${encodeURIComponent(action)}` : ""}`);
  const [verified, setVerified] = useState<any>(null);
  const verify = useAction(async () => setVerified(await api("/api/v1/audit/verify")));
  return (
    <div className="space-y-4">
      <PageHeader title="Audit Log" subtitle="Append-only and hash-chained: any modified or deleted entry breaks verification."
        actions={<Button busy={verify.pending} onClick={() => verify.run()}>Verify chain</Button>} />
      {verified && (
        <div className="text-sm">Chain <Badge value={verified.valid ? "passed" : "failed"} /> — {verified.events_checked} event(s) checked{verified.broken_at_seq ? `, broken at #${verified.broken_at_seq}` : ""}</div>
      )}
      <input aria-label="Filter by action" className={`${inputClass} max-w-xs`} placeholder="filter by action prefix, e.g. approval" value={action} onChange={(e) => setAction(e.target.value)} />
      <ErrorBox error={error ?? verify.error} />
      <Card>
        {loading && !data ? <Spinner /> : (
          <Table head={["#", "Time", "Action", "Outcome", "Resource", "Actor", "Details", "Hash"]} empty={!data?.items.length}>
            {data?.items.map((e: any) => (
              <tr key={e.seq}>
                <Td className="tabular-nums text-xs">{e.seq}</Td>
                <Td className="text-xs">{dateTime(e.created_at)}</Td>
                <Td className="font-mono text-xs">{e.action}</Td>
                <Td><Badge value={e.outcome === "success" ? "ok" : e.outcome} /></Td>
                <Td className="text-xs">{e.resource_type} <span className="font-mono">{shortId(e.resource_id)}</span></Td>
                <Td className="font-mono text-xs">{shortId(e.actor_id)}</Td>
                <Td className="max-w-xs truncate font-mono text-xs" title={JSON.stringify(e.details)}>{JSON.stringify(e.details)}</Td>
                <Td className="font-mono text-xs">{e.hash.slice(0, 10)}</Td>
              </tr>
            ))}
          </Table>
        )}
      </Card>
    </div>
  );
}
