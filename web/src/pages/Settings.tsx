import { useState } from "react";
import { Badge, Button, Card, Collapsible, ErrorBox, Field, inputClass, JsonView, PageHeader, Table, Td } from "../components/ui";
import { currentCredentials, del, post, put, setCredentials } from "../lib/api";
import { dateTime } from "../lib/format";
import { useAction, useApi } from "../lib/hooks";
import { useSession } from "../lib/session";

const GROUPS: Record<string, string[]> = {
  "Provider configuration": ["default_provider", "default_model", "ollama_enabled", "ollama_base_url", "openai_compat_enabled", "openai_compat_base_url", "openai_compat_api_key", "cloud_providers_enabled", "anthropic_api_key", "anthropic_fallbacks", "model_timeout_seconds", "model_max_retries"],
  "Storage": ["database_url", "data_dir", "knowledge_dir", "sandbox_dir", "sample_data_dir"],
  "Security": ["profile", "auth_mode", "host", "oidc_issuer", "oidc_audience", "oidc_jwks_url", "allow_self_approval", "network_allowlist", "allow_private_network", "sandbox_backend", "secrets_backend", "max_request_bytes", "execution_rate_limit_per_minute", "cors_origins"],
  "Execution defaults": ["worker_mode", "worker_concurrency", "default_max_steps", "default_timeout_seconds", "approval_expiry_seconds"],
  "Telemetry": ["telemetry_exporter", "otlp_endpoint", "capture_content", "log_level", "log_json"],
  "Data retention": ["retention_days"],
};

export default function Settings() {
  const { me, can, reload: reloadMe } = useSession();
  const settings = useApi<any>(can("settings:read") ? "/api/v1/system/settings" : null);
  const secrets = useApi<any>(can("settings:read") ? "/api/v1/secrets" : null);
  const keys = useApi<any[]>(me ? "/api/v1/api-keys" : null);
  const creds = currentCredentials();
  const [token, setToken] = useState(creds.token ?? "");
  const [workspace, setWorkspace] = useState(creds.workspace ?? "");
  const [secret, setSecret] = useState({ name: "", value: "" });
  const [newKey, setNewKey] = useState<string | null>(null);
  const toggleTool = useAction(async (name: string, disabled: boolean) => {
    const current = new Set<string>(settings.data.tools.disabled);
    if (disabled) current.add(name); else current.delete(name);
    await put("/api/v1/system/settings/tools", { disabled: [...current] });
    settings.reload();
  });
  const saveSecret = useAction(async () => {
    await post("/api/v1/secrets", secret);
    setSecret({ name: "", value: "" });
    secrets.reload();
  });
  const createKey = useAction(async () => {
    const r = await post("/api/v1/api-keys", { name: `key-${new Date().toISOString().slice(0, 10)}` });
    setNewKey(r.key);
    keys.reload();
  });
  const config = settings.data?.configuration ?? {};

  return (
    <div className="space-y-4">
      <PageHeader title="Settings" subtitle="Most settings are AF_* environment variables (see .env.example) and need a restart. Secret values are never displayed." />
      <Card title="Connection (this browser)">
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="API key" hint="Needed when the server runs with AF_AUTH_MODE=api_key. Stored in this browser only.">
            <input type="password" className={inputClass} value={token} onChange={(e) => setToken(e.target.value)} placeholder="af_…" />
          </Field>
          <Field label="Workspace (slug or id)"><input className={inputClass} value={workspace} onChange={(e) => setWorkspace(e.target.value)} placeholder="default" /></Field>
        </div>
        <Button className="mt-3" onClick={() => { setCredentials(token || null, workspace || null); reloadMe(); window.location.reload(); }}>Save connection</Button>
      </Card>
      {settings.data && (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            {Object.entries(GROUPS).map(([group, fields]) => (
              <Card key={group} title={group}>
                <dl className="space-y-1 text-sm">
                  {fields.filter((f) => f in config).map((f) => (
                    <div key={f} className="flex justify-between gap-3"><dt className="font-mono text-xs text-slate-500">{f}</dt>
                      <dd className="max-w-[60%] truncate text-right text-xs">{typeof config[f] === "object" && config[f] !== null ? (config[f].configured !== undefined ? <Badge value={config[f].configured ? "configured" : "not set"} /> : JSON.stringify(config[f])) : String(config[f])}</dd></div>
                  ))}
                </dl>
              </Card>
            ))}
          </div>
          <Card title="Tool permissions (platform-wide)">
            <Table head={["Tool", "Enabled", ""]}>
              {settings.data.tools.available.map((name: string) => {
                const disabled = settings.data.tools.disabled.includes(name);
                return (
                  <tr key={name}><Td className="font-mono text-xs">{name}</Td><Td><Badge value={disabled ? "disabled" : "ok"} /></Td>
                    <Td>{can("settings:write") && <Button variant="ghost" busy={toggleTool.pending} onClick={() => toggleTool.run(name, !disabled)}>{disabled ? "Enable" : "Disable"}</Button>}</Td></tr>
                );
              })}
            </Table>
            <ErrorBox error={toggleTool.error} />
          </Card>
          <Card title="Workspace"><JsonView value={settings.data.workspace} maxHeight="10rem" /></Card>
        </>
      )}
      {secrets.data && (
        <Card title={`Secrets (backend: ${secrets.data.backend})`}>
          <Table head={["Name", "Status", "Last rotated"]} empty={!secrets.data.items.length}>
            {secrets.data.items.map((s: any) => <tr key={s.name}><Td className="font-mono text-xs">{s.name}</Td><Td><Badge value="configured" /></Td><Td className="text-xs">{dateTime(s.last_rotated_at)}</Td></tr>)}
          </Table>
          {can("settings:write") && (
            <div className="mt-3 flex flex-wrap gap-2">
              <input aria-label="Secret name" className={`${inputClass} max-w-[14rem]`} placeholder="name" value={secret.name} onChange={(e) => setSecret({ ...secret, name: e.target.value })} />
              <input aria-label="Secret value" type="password" className={`${inputClass} max-w-[18rem]`} placeholder="value" value={secret.value} onChange={(e) => setSecret({ ...secret, value: e.target.value })} />
              <Button busy={saveSecret.pending} onClick={() => saveSecret.run()}>Store secret</Button>
            </div>
          )}
          <div className="mt-2"><ErrorBox error={saveSecret.error} /></div>
        </Card>
      )}
      <Card title="Your API keys" actions={<Button busy={createKey.pending} onClick={() => createKey.run()}>Create key</Button>}>
        {newKey && <div className="mb-3 rounded-lg bg-amber-50 p-3 text-sm dark:bg-amber-950">Copy this key now — it is shown once: <code className="break-all">{newKey}</code></div>}
        <Table head={["Name", "Prefix", "Created", "Last used", "Status", ""]} empty={!keys.data?.length}>
          {keys.data?.map((k) => (
            <tr key={k.id}><Td>{k.name}</Td><Td className="font-mono text-xs">{k.prefix}</Td><Td className="text-xs">{dateTime(k.created_at)}</Td><Td className="text-xs">{dateTime(k.last_used_at)}</Td>
              <Td><Badge value={k.revoked ? "disabled" : "ok"} /></Td><Td>{!k.revoked && <Button variant="ghost" className="text-rose-600" onClick={() => del(`/api/v1/api-keys/${k.id}`).then(keys.reload)}>Revoke</Button>}</Td></tr>
          ))}
        </Table>
        <ErrorBox error={createKey.error} />
      </Card>
      <Collapsible title="Raw configuration (safe view)"><JsonView value={config} /></Collapsible>
    </div>
  );
}
