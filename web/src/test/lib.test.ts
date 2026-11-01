import { describe, expect, it, vi } from "vitest";
import { api, ApiError } from "../lib/api";
import { duration, exampleFromSchema, pct } from "../lib/format";
import { statusFromEvents } from "../components/WorkflowGraph";

describe("format helpers", () => {
  it("formats durations and percentages", () => {
    expect(duration(250)).toBe("250 ms");
    expect(duration(2500)).toBe("2.5 s");
    expect(pct(0.834)).toBe("83%");
    expect(pct(null)).toBe("—");
  });

  it("builds example inputs from JSON Schema", () => {
    const schema = { type: "object", required: ["a", "n"], properties: { a: { type: "string" }, n: { type: "integer" }, opt: { type: "string" } } };
    expect(exampleFromSchema(schema)).toEqual({ a: "", n: 0 });
  });
});

describe("api client", () => {
  it("parses the structured error format", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({
      error: { code: "policy_denied", message: "Denied", details: [{ path: "x" }], request_id: "req-1" },
    }), { status: 403 })));
    const err = await api("/api/v1/x").catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(403);
    expect(err.code).toBe("policy_denied");
    expect(err.requestId).toBe("req-1");
    vi.unstubAllGlobals();
  });

  it("sends JSON bodies", async () => {
    const fetchMock = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await api("/api/v1/y", { method: "POST", body: { a: 1 } });
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect(init.body).toBe('{"a":1}');
    vi.unstubAllGlobals();
  });
});

describe("workflow status derivation", () => {
  it("maps events to node states", () => {
    const status = statusFromEvents([
      { type: "execution.started" },
      { type: "step.started", node_id: "a" },
      { type: "step.completed", node_id: "a", status: "completed" },
      { type: "step.started", node_id: "b" },
      { type: "step.paused", node_id: "b", status: "waiting_approval" },
      { type: "step.started", node_id: "c" },
      { type: "step.failed", node_id: "c", status: "failed" },
    ]);
    expect(status).toMatchObject({ __start__: "completed", a: "completed", b: "waiting_approval", c: "failed" });
  });
});
