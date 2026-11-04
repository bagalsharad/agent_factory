import { useState } from "react";
import { Badge, Button, Card, Empty, ErrorBox, Field, inputClass, PageHeader, Spinner, Table, Td } from "../components/ui";
import { del, post, put } from "../lib/api";
import { timeAgo } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

const RISKS = ["low", "medium", "high", "critical"];
const EFFECTS = ["read", "write", "external"];

export default function Connectors() {
  const { data, error, reload } = useApi<any>("/api/v1/connectors/mcp");
  const { can } = useSession();
  const admin = can("settings:write");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Spinner />;
  return (
    <div className="space-y-4">
      <PageHeader
        title="MCP connectors"
        subtitle="MCP tools are exposed only from trusted servers, only when allow-listed, and default to high risk + write (human approval). Every call goes through the same policy engine as built-in tools."
      />
      {!data.items.length && <Empty>No MCP servers registered.</Empty>}
      {data.items.map((s: any) => <ServerCard key={s.id} server={s} admin={admin} reload={reload} />)}
      {admin && <RegisterCard reload={reload} />}
    </div>
  );
}

function ServerCard({ server, admin, reload }: { server: any; admin: boolean; reload: () => void }) {
  const act = useAction(async (fn: () => Promise<unknown>) => { await fn(); reload(); });
  const configure = (body: any) => act.run(() => put(`/api/v1/connectors/mcp/${server.id}`, body));
  const allow = (name: string, on: boolean) =>
    configure({ tool_allowlist: on ? [...server.tool_allowlist, name] : server.tool_allowlist.filter((t: string) => t !== name) });
  return (
    <Card
      title={<span className="flex items-center gap-2">{server.name}
        <Badge value={server.trusted ? "passed" : "pending"} title={server.trusted ? "trusted" : "untrusted"} />
        <span className="text-xs font-normal text-slate-500">{server.transport} · health {server.health?.status ?? "unknown"} {server.health?.checked_at && `(${timeAgo(server.health.checked_at)})`}</span></span>}
      actions={admin && <div className="flex gap-1">
        <Button busy={act.pending} onClick={() => act.run(() => post(`/api/v1/connectors/mcp/${server.id}/discover`))}>Discover tools</Button>
        <Button onClick={() => configure({ trusted: !server.trusted })}>{server.trusted ? "Revoke trust" : "Trust server"}</Button>
        <Button onClick={() => configure({ enabled: !server.enabled })}>{server.enabled ? "Disable" : "Enable"}</Button>
        <Button variant="ghost" className="text-rose-600" onClick={() => act.run(() => del(`/api/v1/connectors/mcp/${server.id}`))}>Remove</Button>
      </div>}
    >
      <div className="mb-2 text-xs text-slate-500">
        {server.transport === "stdio" ? <span className="font-mono">{server.command.join(" ")}</span> : <span className="font-mono">{server.url}</span>}
        {server.env_secrets.length > 0 && <> · credentials from secrets: {server.env_secrets.join(", ")}</>}
        {server.auth_secret && <> · bearer token secret: {server.auth_secret}</>}
      </div>
      <ErrorBox error={act.error} />
      <Table head={["Tool", "Schema", "Server hints", "Risk", "Side effects", "Allowed", "Exposed as"]} empty={!server.tools.length}>
        {server.tools.map((t: any) => (
          <tr key={t.name}>
            <Td><div className="font-mono text-xs">{t.name}</div><div className="text-xs text-slate-500">{t.description}</div></Td>
            <Td>{t.schema_valid ? <Badge value="passed" title="valid" /> : <span className="text-xs text-rose-600">{t.problems.join("; ")}</span>}</Td>
            <Td className="text-xs text-slate-500">{Object.keys(t.annotations ?? {}).join(", ") || "—"}</Td>
            <Td>{admin ? <select className={inputClass} value={t.risk} onChange={(e) => configure({ tool_settings: { [t.name]: { risk: e.target.value, side_effects: t.side_effects } } })}>
              {RISKS.map((r) => <option key={r}>{r}</option>)}</select> : t.risk}</Td>
            <Td>{admin ? <select className={inputClass} value={t.side_effects} onChange={(e) => configure({ tool_settings: { [t.name]: { risk: t.risk, side_effects: e.target.value } } })}>
              {EFFECTS.map((r) => <option key={r}>{r}</option>)}</select> : t.side_effects}</Td>
            <Td><input type="checkbox" disabled={!admin || !t.schema_valid} checked={server.tool_allowlist.includes(t.name)} onChange={(e) => allow(t.name, e.target.checked)} /></Td>
            <Td className="font-mono text-xs">{t.exposed ? t.platform_name : "—"}</Td>
          </tr>
        ))}
      </Table>
    </Card>
  );
}

function RegisterCard({ reload }: { reload: () => void }) {
  const [form, setForm] = useState({ name: "", transport: "http", url: "", command: "", auth_secret: "" });
  const register = useAction(async () => {
    await post("/api/v1/connectors/mcp", {
      name: form.name, transport: form.transport,
      url: form.transport === "http" ? form.url : null,
      command: form.transport === "stdio" ? form.command.split(" ").filter(Boolean) : [],
      auth_secret: form.auth_secret || null,
    });
    setForm({ ...form, name: "" });
    reload();
  });
  return (
    <Card title="Register a server">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-5">
        <Field label="Name"><input className={inputClass} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="crm" /></Field>
        <Field label="Transport"><select className={inputClass} value={form.transport} onChange={(e) => setForm({ ...form, transport: e.target.value })}><option>http</option><option>stdio</option></select></Field>
        {form.transport === "http"
          ? <Field label="URL"><input className={inputClass} value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="https://mcp.example.com/mcp" /></Field>
          : <Field label="Command" hint="Executable must be in AF_MCP_ALLOWED_COMMANDS"><input className={inputClass} value={form.command} onChange={(e) => setForm({ ...form, command: e.target.value })} /></Field>}
        <Field label="Token secret (optional)"><input className={inputClass} value={form.auth_secret} onChange={(e) => setForm({ ...form, auth_secret: e.target.value })} /></Field>
        <div className="flex items-end"><Button variant="primary" busy={register.pending} disabled={!form.name} onClick={() => register.run()}>Register</Button></div>
      </div>
      <p className="mt-2 text-xs text-slate-500">New servers are untrusted: discover their tools, review them, then trust the server and allow individual tools.</p>
      <ErrorBox error={register.error} />
    </Card>
  );
}
