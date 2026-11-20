import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ContinuityPanel } from "../components/ContinuityPanel";
import { SessionProvider } from "../lib/session";

const ME = { user_id: "u1", display_name: "Op", role: "admin", auth_method: "local", workspace: null, permissions: ["*"] };
const VIEW = {
  enabled: true, status: "needs_review",
  intent: [{ version: 1, intent: { goals: { objective: "Place the order at the current price.", input: { item: "widget" } } }, reason: "initial intent" }],
  graph: [
    { step_id: "fetch", kind: "tool", depends_on: { input: true, steps: [] }, records: [{ validity: "invalidated", side_effects: [] }, { validity: "valid", side_effects: [] }] },
    { step_id: "act", kind: "tool", depends_on: { input: true, steps: ["fetch"] }, records: [{ validity: "valid", side_effects: [{ tool: "place_order" }] }] },
  ],
  assessments: [{
    decision: "escalate", from_step: "fetch", trigger: "recover", status: "needs_review", created_at: new Date().toISOString(),
    rationale: "Steps from 'fetch' need to be redone, but that would repeat committed side effects (place_order)",
    scores: { scs: 0.84, hard_invariants: 1, dimensions: { goal_preservation: 1, constraint_preservation: 1, decision_consistency: 0.6, action_integrity: 1, state_consistency: 1 } },
    changes: [{ type: "tool", step: "fetch", detail: "tool 'quote' contract changed (1.0.0 → 2.0.0)" }],
    findings: [{ check: "action_integrity", status: "pass", severity: "info", detail: "1 committed side effect(s), none duplicated or in doubt" }],
  }],
};

afterEach(() => vi.unstubAllGlobals());

describe("ContinuityPanel", () => {
  it("explains the decision, the score and lets an operator decide", async () => {
    vi.stubGlobal("fetch", vi.fn(async (url: string) =>
      new Response(JSON.stringify(url.startsWith("/api/v1/me") ? ME : VIEW), { status: 200 })));
    render(<MemoryRouter><SessionProvider><ContinuityPanel executionId="e1" status="needs_review" onChange={() => {}} /></SessionProvider></MemoryRouter>);
    expect(await screen.findByText(/would repeat committed side effects/)).toBeInTheDocument();
    expect(screen.getByText("0.84")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: "Decisions" })).toHaveAttribute("aria-valuenow", "0.6");
    expect(screen.getByText(/contract changed/)).toBeInTheDocument();
    expect(screen.getByText("replayed 1×")).toBeInTheDocument();
    expect(await screen.findByText("This execution is held for review. How should it continue?")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Replay from fetch" })).toBeDisabled(); // a reason is required first
  });
});
