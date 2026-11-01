import { useState } from "react";
import { Button, Card, Empty, ErrorBox, Field, inputClass, PageHeader, Table, Td } from "../components/ui";
import { api, del, post } from "../lib/api";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function Knowledge() {
  const { data: collections, reload } = useApi<any[]>("/api/v1/knowledge/collections");
  const [selected, setSelected] = useState<string>("");
  const active = selected || collections?.[0]?.name || "";
  const docs = useApi<any[]>(active ? `/api/v1/knowledge/documents?collection=${encodeURIComponent(active)}` : null);
  const { can } = useSession();
  const [name, setName] = useState("research-docs");
  const [directory, setDirectory] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<any[] | null>(null);
  const ingest = useAction(async () => {
    const r = await post(`/api/v1/knowledge/collections/${encodeURIComponent(name)}/ingest`, { directory });
    reload();
    docs.reload();
    return r;
  });
  const upload = useAction(async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    await api(`/api/v1/knowledge/collections/${encodeURIComponent(name)}/upload`, { method: "POST", form });
    reload();
    docs.reload();
  });
  const search = useAction(async () => setHits(await post("/api/v1/knowledge/search", { query, collections: [active], top_k: 5 })));
  const remove = useAction(async (id: string) => {
    await del(`/api/v1/knowledge/documents/${id}`);
    docs.reload();
    reload();
  });

  return (
    <div className="space-y-4">
      <PageHeader title="Knowledge" subtitle="Workspace document collections used by retrieval agents. Retrieved text is always treated as untrusted data." />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Collections">
          {!collections?.length ? <Empty>No collections yet.</Empty> : (
            <ul className="space-y-1 text-sm">
              {collections.map((c) => (
                <li key={c.id}><button className={active === c.name ? "font-semibold text-indigo-600" : ""} onClick={() => setSelected(c.name)}>{c.name}</button>
                  <span className="text-xs text-slate-500"> · {c.documents} document(s)</span></li>
              ))}
            </ul>
          )}
        </Card>
        {can("knowledge:write") && (
          <Card title="Ingest documents" className="lg:col-span-2">
            <div className="grid gap-3 md:grid-cols-2">
              <Field label="Collection"><input className={inputClass} value={name} onChange={(e) => setName(e.target.value)} /></Field>
              <Field label="Directory" hint="Must be inside AF_KNOWLEDGE_DIR or the bundled examples (no unrestricted filesystem access).">
                <input className={inputClass} placeholder="/path/to/approved/folder" value={directory} onChange={(e) => setDirectory(e.target.value)} />
              </Field>
            </div>
            <div className="mt-3 flex flex-wrap items-center gap-3">
              <Button variant="primary" disabled={!directory} busy={ingest.pending} onClick={() => ingest.run()}>Ingest directory</Button>
              <label className="text-sm text-indigo-600">
                <input type="file" className="hidden" accept=".txt,.md,.pdf,.html,.json,.csv" onChange={(e) => e.target.files?.[0] && upload.run(e.target.files[0])} />
                <span className="cursor-pointer hover:underline">or upload a file…</span>
              </label>
            </div>
            <div className="mt-2"><ErrorBox error={ingest.error ?? upload.error} /></div>
          </Card>
        )}
      </div>
      {active && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card title={`Documents in ${active}`}>
            <Table head={["Title", "Source", "Characters", ""]} empty={!docs.data?.length}>
              {docs.data?.map((d) => (
                <tr key={d.document_id}><Td>{d.title}</Td><Td className="font-mono text-xs">{d.source}</Td><Td className="tabular-nums">{d.chars}</Td>
                  <Td>{can("knowledge:write") && <Button variant="ghost" className="text-rose-600" onClick={() => remove.run(d.document_id)}>Delete</Button>}</Td></tr>
              ))}
            </Table>
          </Card>
          <Card title="Search">
            <div className="flex gap-2">
              <input aria-label="Search query" className={inputClass} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="e.g. freshness SLA for gold pipelines" />
              <Button variant="primary" disabled={!query} busy={search.pending} onClick={() => search.run()}>Search</Button>
            </div>
            <ul className="mt-3 space-y-3 text-sm">
              {hits?.map((h, i) => (
                <li key={i} className="rounded-lg bg-slate-50 p-2 dark:bg-slate-800/50">
                  <div className="text-xs text-slate-500">{h.source} · chunk {h.chunk_index} · score {h.score}</div>
                  <div className="line-clamp-4">{h.text}</div>
                </li>
              ))}
              {hits && !hits.length && <Empty>No matching passages.</Empty>}
            </ul>
          </Card>
        </div>
      )}
    </div>
  );
}
