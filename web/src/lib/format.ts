export function timeAgo(iso?: string | null): string {
  if (!iso) return "—";
  const seconds = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function dateTime(iso?: string | null): string {
  return iso ? new Date(iso).toLocaleString() : "—";
}

export function duration(ms?: number | null): string {
  if (ms === null || ms === undefined) return "—";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

export function shortId(id?: string | null): string {
  return id ? id.slice(0, 8) : "—";
}

export function pct(value?: number | null): string {
  return value === null || value === undefined ? "—" : `${Math.round(value * 100)}%`;
}

export const ACTIVE_STATUSES = new Set(["queued", "running", "waiting_approval"]);

/** Build an example input object from a JSON Schema (for the run form). */
export function exampleFromSchema(schema: any): any {
  if (!schema || typeof schema !== "object") return {};
  if (schema.example !== undefined) return schema.example;
  switch (schema.type) {
    case "object": {
      const out: Record<string, unknown> = {};
      for (const [key, sub] of Object.entries<any>(schema.properties ?? {})) {
        if ((schema.required ?? []).includes(key)) out[key] = exampleFromSchema(sub);
      }
      return out;
    }
    case "array":
      return [exampleFromSchema(schema.items)];
    case "number":
    case "integer":
      return 0;
    case "boolean":
      return false;
    default:
      return schema.enum?.[0] ?? "";
  }
}
