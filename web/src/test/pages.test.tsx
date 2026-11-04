import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppRoutes } from "../App";
import { SessionProvider } from "../lib/session";

const ME = { user_id: "u1", display_name: "Local developer", role: "admin", auth_method: "local",
  workspace: { id: "w", slug: "default", name: "Default workspace" }, permissions: ["*"] };

function mockApi(routes: Record<string, unknown>) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, init });
    const key = Object.keys(routes).find((k) => url.startsWith(k));
    if (!key) return new Response(JSON.stringify({ items: [], total: 0 }), { status: 200 });
    return new Response(JSON.stringify(routes[key]), { status: 200 });
  }));
  return calls;
}

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><SessionProvider><AppRoutes /></SessionProvider></MemoryRouter>);
}

afterEach(() => vi.unstubAllGlobals());

describe("pages", () => {
  it("renders the dashboard metrics", async () => {
    mockApi({
      "/api/v1/me": ME,
      "/api/v1/dashboard": {
        agents: { total: 7, published: 6, versions_by_status: {} },
        executions: { active: 1, failed: 2, completed: 9, by_status: {} },
        recent_executions: [{ id: "abcdef123456", agent_name: "data-pipeline-diagnostic", version: "1.0.0", status: "completed", duration_ms: 120, created_at: new Date().toISOString() }],
        pending_approvals: { total: 1, items: [] },
        evaluations: { recent_runs: 3, passed: 3, failed: 0, running: 0 },
        providers: [{ provider: "mock", status: "up", local: true, detail: "" }],
        runtime: { worker_healthy: true, worker_mode: "embedded", database: "sqlite" },
      },
    });
    renderAt("/");
    expect(await screen.findByText("Total agents")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(screen.getByText("data-pipeline-diagnostic")).toBeInTheDocument();
    expect(screen.getByText("1 awaiting approval")).toBeInTheDocument();
  });

  it("approves a pending action with a comment", async () => {
    const calls = mockApi({
      "/api/v1/me": ME,
      "/api/v1/approvals/ap1/approve": { status: "approved" },
      "/api/v1/approvals": { total: 1, items: [{ id: "ap1", execution_id: "ex123456789", action: "production_restart", tool_name: "restart_pipeline",
        step_id: "request_restart", risk: "high", status: "pending", rationale: "Transient failure", arguments: { pipeline_id: "orders_daily" },
        evidence: {}, requested_at: new Date().toISOString(), expires_at: new Date().toISOString(), requested_by: "u1" }] },
    });
    renderAt("/approvals");
    fireEvent.change(await screen.findByLabelText("Decision comment"), { target: { value: "looks transient" } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(calls.some((c) => c.url === "/api/v1/approvals/ap1/approve")).toBe(true));
    const call = calls.find((c) => c.url === "/api/v1/approvals/ap1/approve")!;
    expect(JSON.parse(String(call.init?.body))).toEqual({ comment: "looks transient" });
  });

  it("generates a draft in the designer", async () => {
    const calls = mockApi({
      "/api/v1/me": ME,
      "/api/v1/templates": [{ name: "custom-agent", display_name: "Custom Agent" }],
      "/api/v1/agents/draft": { spec: { metadata: { name: "x-agent", display_name: "X" }, spec: { objective: "Do things well", instructions: "",
        inputs: {}, outputs: {}, model: { provider: "mock", model: "mock-default" }, tools: [], workflow: { type: "single", steps: [] } } },
        yaml: "metadata: {}", template: "custom-agent@1.0.0", confidence: 0.3, rationale: ["r"], assumptions: ["a"], model_assisted: false },
      "/api/v1/agents/preview": { spec: {}, yaml: "", validation: { valid: true, errors: [], warnings: [] }, workflow: { nodes: [], edges: [] } },
    });
    renderAt("/designer");
    fireEvent.change(await screen.findByLabelText("Objective"), { target: { value: "Create an agent that summarizes reports" } });
    fireEvent.click(screen.getByRole("button", { name: "Generate draft specification" }));
    expect(await screen.findByText(/confidence 30%/)).toBeInTheDocument();
    expect(calls.some((c) => c.url === "/api/v1/agents/draft")).toBe(true);
  });

  it("renders operations alerts and agent health", async () => {
    const metrics = { executions: 4, by_status: { completed: 3, failed: 1 }, success_rate: 0.75, failure_rate: 0.25,
      timeout_rate: 0, retry_rate: 0.25, retries: 1, p50_latency_ms: 900, p95_latency_ms: 1800, input_tokens: 100,
      output_tokens: 20, model_calls: 4, cost_usd: 0, cost_per_execution_usd: 0, policy_violations: 2,
      approvals: { requested: 1, approved: 1, rejected: 0, approval_rate: 1 },
      tools: { read_pipeline_logs: { calls: 4, success_rate: 1, failed: 0, denied: 0, in_doubt: 0 } } };
    mockApi({
      "/api/v1/me": ME,
      "/api/v1/operations/metrics": { window_hours: 24, workspace: metrics,
        agents: [{ agent_id: "a1", agent: "data-pipeline-diagnostic", evaluation_pass_rate: 1, ...metrics }] },
      "/api/v1/operations/alerts": { critical: 1, items: [{ severity: "critical", kind: "slo_breached", agent_id: "a1",
        message: "SLO 'pipeline-success' breached" }] },
      "/api/v1/operations/providers": { providers: [{ provider: "mock", status: "up", circuit: { state: "closed" } }], circuits: [] },
    });
    renderAt("/operations");
    expect(await screen.findByText("SLO 'pipeline-success' breached")).toBeInTheDocument();
    expect(screen.getAllByText("75%").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "data-pipeline-diagnostic" })).toBeInTheDocument();
  });

  it("shows MCP tools as unexposed until the server is trusted", async () => {
    mockApi({
      "/api/v1/me": ME,
      "/api/v1/connectors/mcp": { items: [{ id: "s1", name: "shop", transport: "stdio", command: ["python", "srv.py"],
        url: null, env_secrets: ["SHOP_TOKEN"], auth_secret: null, trusted: false, enabled: true, tool_allowlist: [],
        health: { status: "ok" }, tools: [{ name: "refund_order", description: "Refund", schema_valid: true, problems: [],
          annotations: {}, risk: "high", side_effects: "write", exposed: false, platform_name: "mcp__shop__refund_order" }] }] },
    });
    renderAt("/connectors");
    expect(await screen.findByText("refund_order")).toBeInTheDocument();
    expect(screen.getByText("Trust server")).toBeInTheDocument();
    expect(screen.getByText(/credentials from secrets: SHOP_TOKEN/)).toBeInTheDocument();
  });

  it("opens the command palette with Cmd+K and jumps to a page", async () => {
    mockApi({ "/api/v1/me": ME, "/api/v1/agents": { items: [{ id: "a1", name: "interview-prep", display_name: "Interview Prep" }], total: 1 } });
    renderAt("/approvals");
    fireEvent.keyDown(window, { key: "k", metaKey: true });
    const search = await screen.findByLabelText("Search pages and agents");
    fireEvent.change(search, { target: { value: "interview" } });
    expect(await screen.findByText("Interview Prep")).toBeInTheDocument();
    fireEvent.change(search, { target: { value: "execution monitor" } });
    fireEvent.keyDown(search, { key: "Enter" });
    await waitFor(() => expect(screen.queryByLabelText("Search pages and agents")).not.toBeInTheDocument());
  });
});
