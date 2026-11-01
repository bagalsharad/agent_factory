import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Badge, Button, Card, ErrorBox, inputClass, JsonView, PageHeader, Table, Tabs, Td } from "../components/ui";
import { post } from "../lib/api";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

export default function Catalog() {
  const [tab, setTab] = useState("templates");
  return (
    <div className="space-y-4">
      <PageHeader title="Tools & Templates" subtitle="Registered tools, reusable agent templates and versioned components." />
      <Tabs tabs={[{ id: "templates", label: "Templates" }, { id: "tools", label: "Tools" }, { id: "components", label: "Components" }]} active={tab} onChange={setTab} />
      {tab === "templates" && <Templates />}
      {tab === "tools" && <Tools />}
      {tab === "components" && <Components />}
    </div>
  );
}

function Templates() {
  const { data } = useApi<any[]>("/api/v1/templates");
  const navigate = useNavigate();
  const { can } = useSession();
  const create = useAction(async (template: string) => {
    const name = window.prompt("Agent name (lowercase, dashes)", template.replace(/-agent$/, "") + "-agent");
    if (!name) return;
    const agent = await post("/api/v1/agents", { template, name });
    navigate(`/designer/${agent.id}`);
  });
  return (
    <>
      <ErrorBox error={create.error} />
      <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-3">
        {data?.map((t) => (
          <Card key={t.name} title={<span className="flex items-center gap-2">{t.display_name}<Badge value={t.category} /></span>}
            actions={can("agents:write") && <Button onClick={() => create.run(t.name)}>Use</Button>}>
            <p className="text-sm text-slate-600 dark:text-slate-300">{t.description}</p>
            <dl className="mt-3 space-y-1 text-xs">
              <div><dt className="inline text-slate-500">Use case: </dt><dd className="inline">{t.use_case}</dd></div>
              <div><dt className="inline text-slate-500">Inputs: </dt><dd className="inline font-mono">{t.required_inputs.join(", ") || "—"}</dd></div>
              <div><dt className="inline text-slate-500">Tools: </dt><dd className="inline font-mono">{t.tool_requirements.join(", ") || "none"}</dd></div>
              <div><dt className="inline text-slate-500">Workflow: </dt><dd className="inline">{t.workflow_type}</dd></div>
              <div><dt className="inline text-slate-500">Security: </dt><dd className="inline"><Badge value={t.security_profile.risk} /> {t.security_profile.summary}</dd></div>
              <div><dt className="inline text-slate-500">Evaluation examples: </dt><dd className="inline">{t.evaluation_examples.length}</dd></div>
              <div><dt className="inline text-slate-500">Source: </dt><dd className="inline">{t.source} · v{t.version}</dd></div>
            </dl>
            {t.notes && <p className="mt-2 text-xs text-amber-700 dark:text-amber-300">{t.notes}</p>}
          </Card>
        ))}
      </div>
    </>
  );
}

function Tools() {
  const { data } = useApi<any[]>("/api/v1/tools");
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card>
      <Table head={["Tool", "Connector", "Risk", "Side effects", "Approval class", "Permissions", "Timeout", "Status", ""]}>
        {data?.flatMap((t) => [
          <tr key={t.name}>
            <Td><div className="font-mono text-xs">{t.name}@{t.version}</div><div className="text-xs text-slate-500">{t.description}</div></Td>
            <Td className="text-xs">{t.connector}</Td>
            <Td><Badge value={t.risk} /></Td>
            <Td className="text-xs">{t.side_effects}{t.network ? " · network" : ""}{t.requires_sandbox ? " · sandbox" : ""}</Td>
            <Td className="text-xs">{t.action_class ?? "—"}</Td>
            <Td className="text-xs">{t.permissions.join(", ")}</Td>
            <Td className="text-xs">{t.timeout_seconds}s</Td>
            <Td><Badge value={t.enabled ? "ok" : "disabled"} /></Td>
            <Td><Button variant="ghost" onClick={() => setOpen(open === t.name ? null : t.name)}>Schema</Button></Td>
          </tr>,
          open === t.name && <tr key={`${t.name}-s`}><Td colSpan={9}><div className="grid gap-3 md:grid-cols-2"><JsonView value={t.input_schema} maxHeight="14rem" /><JsonView value={t.output_schema} maxHeight="14rem" /></div></Td></tr>,
        ])}
      </Table>
    </Card>
  );
}

const COMPONENT_EXAMPLE = JSON.stringify({ kind: "system_instruction", name: "my-instruction", version: "1.0.0", description: "", definition: { text: "Be precise." } }, null, 2);

function Components() {
  const { data, reload } = useApi<any[]>("/api/v1/components");
  const { can } = useSession();
  const [text, setText] = useState(COMPONENT_EXAMPLE);
  const create = useAction(async () => {
    await post("/api/v1/components", JSON.parse(text));
    reload();
  });
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <Card title="Components (immutable per version)" className="lg:col-span-2">
        <Table head={["Reference", "Description", "Status", "Pinned by", "Scope"]} empty={!data?.length}>
          {data?.map((c) => (
            <tr key={c.id}>
              <Td className="font-mono text-xs">{c.ref}</Td>
              <Td className="text-xs">{c.description}</Td>
              <Td><Badge value={c.status === "active" ? "ok" : c.status} /></Td>
              <Td className="text-xs">{c.used_by_versions} version(s)</Td>
              <Td className="text-xs">{c.builtin ? "built-in" : "workspace"}</Td>
            </tr>
          ))}
        </Table>
      </Card>
      {can("components:write") && (
        <Card title="Register a component">
          <textarea aria-label="Component JSON" className={`${inputClass} h-64 font-mono text-xs`} value={text} onChange={(e) => setText(e.target.value)} />
          <Button className="mt-2" variant="primary" busy={create.pending} onClick={() => create.run()}>Create</Button>
          <div className="mt-2"><ErrorBox error={create.error} /></div>
        </Card>
      )}
    </div>
  );
}
