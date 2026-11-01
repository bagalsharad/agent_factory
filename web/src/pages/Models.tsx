import { useState } from "react";
import { Badge, Button, Card, ErrorBox, Field, inputClass, JsonView, PageHeader, Spinner, Table, Td } from "../components/ui";
import { post } from "../lib/api";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function Models() {
  const { data: providers, reload } = useApi<any[]>("/api/v1/models/providers");
  const { data: models } = useApi<any>("/api/v1/models/available");
  const { data: recs } = useApi<any>("/api/v1/models/recommendations");
  const { can } = useSession();
  const [provider, setProvider] = useState("mock");
  const [model, setModel] = useState("mock-default");
  const [prompt, setPrompt] = useState("Reply with the single word: ready");
  const [answer, setAnswer] = useState<any>(null);
  const test = useAction(async () => setAnswer(await post("/api/v1/models/test", { provider, model, prompt })));

  return (
    <div className="space-y-4">
      <PageHeader title="Models & Providers" subtitle="Local-first: cloud providers stay disabled until an operator explicitly enables them. Models are never downloaded automatically."
        actions={<Button onClick={reload}>Re-check connectivity</Button>} />
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
        {providers?.map((p) => (
          <Card key={p.name} title={<span className="flex items-center gap-2">{p.name} <Badge value={p.status} /></span>}>
            <p className="text-sm text-slate-600 dark:text-slate-300">{p.description}</p>
            <p className="mt-2 text-xs"><Badge value={p.local ? "local" : "cloud"} /> {p.latency_ms != null && <span className="text-slate-500">{p.latency_ms} ms</span>}</p>
            <p className="mt-2 text-xs text-slate-500">{p.enabled ? p.detail : p.reason}</p>
          </Card>
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card title="Available models" className="lg:col-span-2">
          {!models ? <Spinner /> : (
            <>
              <Table head={["Provider", "Model", "Tools", "Structured", "Streaming", "Context", "Size"]} empty={!models.models.length}>
                {models.models.map((m: any) => (
                  <tr key={`${m.provider}/${m.name}`}>
                    <Td className="text-xs">{m.provider}</Td>
                    <Td className="font-mono text-xs">{m.name}<div className="font-sans text-slate-500">{m.details?.description}</div></Td>
                    <Td>{m.capabilities.tools ? "yes" : "no"}</Td>
                    <Td>{m.capabilities.structured_output ? "yes" : "no"}</Td>
                    <Td>{m.capabilities.streaming ? "yes" : "no"}</Td>
                    <Td className="text-xs">{m.context_window ?? "—"}</Td>
                    <Td className="text-xs">{m.size_bytes ? `${(m.size_bytes / 1e9).toFixed(1)} GB` : "—"}</Td>
                  </tr>
                ))}
              </Table>
              {Object.entries<string>(models.errors ?? {}).map(([k, v]) => <p key={k} className="mt-2 text-xs text-amber-600">{k}: {v}</p>)}
            </>
          )}
        </Card>
        <Card title="Recommended local models">
          {recs && (
            <>
              <p className="mb-2 text-xs text-slate-500">For this machine ({recs.memory_gb} GB RAM). {recs.note}</p>
              <ul className="space-y-3 text-sm">
                {recs.recommendations.map((r: any) => (
                  <li key={r.model}>
                    <div className="flex items-center gap-2"><span className="font-mono">{r.model}</span>{r.installed ? <Badge value="ok" title="installed" /> : <Badge value="missing" />}</div>
                    <div className="text-xs text-slate-500">{r.why}</div>
                    {!r.installed && <code className="mt-1 block rounded bg-slate-100 px-2 py-1 text-xs dark:bg-slate-800">{r.install}</code>}
                  </li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>
      {can("models:test") && (
        <Card title="Test a model">
          <div className="grid gap-3 md:grid-cols-3">
            <Field label="Provider">
              <select className={inputClass} value={provider} onChange={(e) => setProvider(e.target.value)}>
                {providers?.filter((p) => p.enabled).map((p) => <option key={p.name}>{p.name}</option>)}
              </select>
            </Field>
            <Field label="Model">
              <input list="test-models" className={inputClass} value={model} onChange={(e) => setModel(e.target.value)} />
              <datalist id="test-models">{models?.models?.filter((m: any) => m.provider === provider).map((m: any) => <option key={m.name} value={m.name} />)}</datalist>
            </Field>
            <Field label="Prompt"><input className={inputClass} value={prompt} onChange={(e) => setPrompt(e.target.value)} /></Field>
          </div>
          <Button className="mt-3" variant="primary" busy={test.pending} onClick={() => test.run()}>Send</Button>
          <div className="mt-3 space-y-2"><ErrorBox error={test.error} />{answer && <JsonView value={answer} maxHeight="16rem" />}</div>
        </Card>
      )}
    </div>
  );
}
