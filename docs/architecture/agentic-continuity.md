# Agentic Continuity Engineering (ACE)

Implementation of the framework in *Beyond Autonomous Execution: Agentic Continuity Engineering for Resilient,
Long-Running and Self-Evolving Multi-Agent Systems* (S. H. Bagal, draft v1.0, November 2026).

The premise: restoring execution state is not the same as recovering. Before an interrupted execution continues,
Agent Factory now establishes that the work it would build on is still consistent with the original intent, the
current models, tools, agents and knowledge, and the actions already committed. Otherwise it replays exactly the
affected steps, re-plans, or holds the execution for a person.

## Mapping the paper to the platform

| Paper (§4) | Implementation | Code |
|---|---|---|
| Intent Ledger, I₀ = (G, C, A, P) | `execution_intents`: goals (objective, instructions hash, task input), constraints (limits, data class, write/network policy), acceptance (output contract, acceptance criteria), authorization (requester, role, tools, approval rules), invariants. Version 1 at creation; amendments are new versions by an authorized person (`executions:recover`) with a reason, audited; limits can only be tightened | `continuity/ledger.py` |
| Execution State Graph, vᵢ = (aᵢ, sᵢ, dᵢ, oᵢ, pᵢ) | `step_records`: one row per completed step with its dependencies (from templates and context rules), status, validity, output digest and provenance (tool invocations, side effects) | `continuity/dependencies.py`, `continuity/service.py` |
| Semantic Checkpoint Engine | Each record carries the environment the step ran under (model + weights digest, tool versions and contract hashes, specialist agent version, knowledge fingerprint, spec and policy hashes), its input digest and the intent version. LangGraph checkpoints still hold the state; records are append-only (replayed work is marked `invalidated`, never rewritten) | `continuity/fingerprint.py` |
| Change Impact Analyzer, Eₜ = (Mₜ, Tₜ, Rₜ, Kₜ) | Recomputes Eₜ₊₁ and compares it with every record: model or digest change, tool contract change/removal/disabling, specialist replacement, knowledge update, platform policy change, intent amendment. Maps changes to steps and propagates downstream through the dependency graph | `continuity/assessment.py` (`analyze_changes`) |
| Continuity Validator | Deterministic, evidence-backed findings (no model judges continuity): spec integrity, intent-ledger integrity and authorization, policy re-evaluation of every completed action under current RBAC/policy, duplicate or in-doubt side effects, dependency validity, state consistency (recorded vs current input digests) | `validate` |
| Recovery Decision Engine | resume / revalidate-and-resume / replay from a step / re-plan / escalate (rules below) | `decide`, `runtime/executor.py` (`_continuity_gate`) |
| Continuity Auditor | `continuity_assessments` (append-only), hash-chained audit events `continuity.assessment`, `continuity.decision`, `intent.amend`, `tool.in_doubt`, and execution events `continuity.*` | `continuity/service.py` |
| Semantic Continuity Score (§5) | SCS = H × (w_g·Gp + w_c·Cp + w_d·Dc + w_a·Ai + w_s·Sc), equal weights 0.2; H = 0 on any critical finding; all five components always reported | `score` |
| Experiment (§6) | Baseline A (checkpoint-and-retry) vs B (ACE) on the six failure scenarios with the paper's metrics | `continuity/benchmark.py`, `agent-factory continuity benchmark` |

## When it runs

- **Creation:** intent v1 is written in the same transaction as the execution.
- **Every step:** after a step produces its output, a semantic checkpoint (step record) is written. Bookkeeping
  failures are logged and never fail the step.
- **Before every recovery** (worker crash, operator retry) and **every resume after an approval**, the executor
  runs an assessment and acts on its decision. Child (specialist) executions are assessed through their parent.
- **On demand:** `POST /api/v1/executions/{id}/continuity/assess` (preview; `?persist=true` records it).

`AF_CONTINUITY_ENABLED=false` turns the assessment off (recording continues) and gives conventional recovery.

## Decision rules

Evaluated in order; anything uncertain or irreversible goes to a person.

| Condition | Decision |
|---|---|
| A critical finding (spec or intent tampered, duplicate committed side effect, a committed write no longer authorized) | **escalate** (H = 0) |
| A side-effecting call has an unknown outcome (interrupted mid-call) | **escalate**; the call is marked `in_doubt` for the operator |
| The goals were amended | **replan** (fresh run under the new intent); **escalate** if anything irreversible already happened |
| Steps are affected (changed environment, revoked authorization, inconsistent state) | **replay** from the earliest affected step; **escalate** if that would redo a committed side effect |
| Waiting for approval and anything relevant changed | **escalate** (the approved action may no longer hold) |
| Something changed but no completed step depends on it | **revalidate and resume** |
| Nothing relevant changed | **resume** |

Replay forks the LangGraph thread from the checkpoint taken just before the earliest affected step, marks the
records and read-only tool results of that region `invalidated` so they are redone, and leaves committed side
effects untouched. Steps that do not depend on the change (by the dependency graph) are not redone.

Rollback in the paper's sense (undoing an external action) needs compensating actions the platform does not
have; such cases escalate instead.

## Operator workflow

An escalated execution has status `needs_review` with the assessment in its error details. In the dashboard
(execution page → Continuity) or via the API an operator with `executions:recover`:

- reads what changed, the findings and the score;
- decides `resume`, `replay` (from a step), `replan` or `cancel` with a reason —
  `POST /api/v1/executions/{id}/continuity/decision`. The decision is stored as an `overridden` assessment and
  audited; it cannot skip the in-doubt resolution of an interrupted side effect;
- may amend the intent first — `POST /api/v1/executions/{id}/intent` (new input re-plans; limits only tighten).

`GET /api/v1/executions/{id}/continuity` returns the intent versions, the state graph with every record, and all
assessments.

## Benchmark (paper §6)

`agent-factory continuity benchmark [--runs N] [--output results.json]` runs each scenario in a throwaway
platform with the deterministic mock model, once with conventional recovery (ACE off, in-doubt guard off) and once
with ACE. The workflow quotes a price, decides, crashes, places an irreversible order at the quoted price and
reports. Result of one run per scenario on 2026-11-19:

| Scenario | Conventional | ACE |
|---|---|---|
| Runtime interruption | correct | correct (resume) |
| Model upgrade | stale decision used | correct (replay from the model step) |
| Tool / API contract change | order at the outdated price | correct (replay; order at the new price) |
| Agent replacement | stale specialist advice used | correct (replay the delegation) |
| Outdated shared memory | stale knowledge used | correct (replay the retrieval) |
| Duplicate external action | order placed twice | held for review; order placed once |

| Metric | Conventional | ACE |
|---|---|---|
| Recovery correctness | 0.167 | 0.833 |
| Unsafe action rate | 0.333 | 0.0 |
| Intent drift rate | 0.667 | 0.0 |
| Human escalation rate | 0.0 | 0.167 |
| Mean SCS (assessed after recovery) | 0.947 | 0.967 |
| Mean re-execution overhead (steps beyond the minimum) | 0.0 | 0.0 |
| Mean recovery latency (mock model) | 27 ms | 39 ms |

How to read it:
- These numbers validate the recovery **mechanics** on a deterministic model. They are not evidence about
  model-quality outcomes; run the scenarios with real models (several runs, report spread) before drawing
  research conclusions.
- ACE's one non-correct case is a deliberate escalation, not a wrong outcome.
- The conventional duplicate-order run scores SCS 1.0 because the second order reused the same idempotency
  record: the platform could not see the duplicate. This is the paper's point that a score built from recorded
  evidence needs the action-integrity guard to observe what happened; it also shows why component scores must be
  read alongside the outcome oracle.
- Latency is the cost of assessment plus the replayed steps; with real models the replayed model calls dominate.

## Limitations

- Dependencies come from templates and context rules; a tool that reads hidden shared state is not tracked.
- Model upgrades are detected only where the provider reports a weights digest (Ollama, the mock model);
  OpenAI-compatible and Anthropic models are fingerprinted by name only.
- Rollback of external effects is not supported (no compensating actions); it escalates.
- SCS weights are equal by default and not yet calibrated; the paper calls for experimental validation.
- Assessment adds database reads before each recovery and one write per step.
