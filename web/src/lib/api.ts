// Thin fetch client for the Agent Factory API. Errors follow {"error": {code, message, details, request_id}}.

export class ApiError extends Error {
  status: number;
  code: string;
  details: unknown;
  requestId?: string;

  constructor(status: number, code: string, message: string, details?: unknown, requestId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }
}

const TOKEN_KEY = "af.apiToken";
const WORKSPACE_KEY = "af.workspace";

function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function setCredentials(token: string | null, workspace: string | null): void {
  try {
    if (token) window.localStorage.setItem(TOKEN_KEY, token);
    else window.localStorage.removeItem(TOKEN_KEY);
    if (workspace) window.localStorage.setItem(WORKSPACE_KEY, workspace);
    else window.localStorage.removeItem(WORKSPACE_KEY);
  } catch {
    /* storage unavailable: credentials apply to this page only */
  }
}

export function currentCredentials(): { token: string | null; workspace: string | null } {
  return { token: readStorage(TOKEN_KEY), workspace: readStorage(WORKSPACE_KEY) };
}

export async function api<T = any>(
  path: string,
  options: { method?: string; body?: unknown; headers?: Record<string, string>; form?: FormData } = {},
): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  const { token, workspace } = currentCredentials();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (workspace) headers["X-Workspace-ID"] = workspace;
  let body: BodyInit | undefined;
  if (options.form) body = options.form;
  else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }
  const response = await fetch(path, { method: options.method ?? (body ? "POST" : "GET"), headers, body });
  if (response.status === 204) return undefined as T;
  const text = await response.text();
  const data = text ? JSON.parse(text) : undefined;
  if (!response.ok) {
    const err = data?.error ?? {};
    throw new ApiError(response.status, err.code ?? "http_error", err.message ?? response.statusText, err.details, err.request_id);
  }
  return data as T;
}

export const post = <T = any>(path: string, body?: unknown, headers?: Record<string, string>) =>
  api<T>(path, { method: "POST", body: body ?? {}, headers });
export const put = <T = any>(path: string, body: unknown) => api<T>(path, { method: "PUT", body });
export const del = (path: string) => api(path, { method: "DELETE" });
