# Evaluation architecture

The evaluation engine decides whether an agent version may be published. A run is only as trustworthy as what it
checks, so every gate is deterministic, computed from records the agent cannot write, and enforced by the backend.

## Components

| Component | Code | Role |
|---|---|---|
| Suite registry | `EvaluationService._upsert_suite`, `evaluation_suites` | Content-hashed, versioned suites (inline spec cases, pinned `evaluation_dataset` components, or registered suites) |
| Case runner | `EvaluationService._run_case` | Runs each case through the real runtime in `evaluation` mode: same harness, policies, tools, approvals and budgets as production. Cases can override the model (e.g. `mock-adversarial`), inject faults, pre-decide approvals and tighten limits |
| Observer | `EvaluationService._observe` | Reads what actually happened from `tool_invocations`, `execution_events` and the execution record, never from the agent's own output |
| Deterministic checks | `evaluation/checks.py` | Status, error code, output schema, fields, contains / not contains, tools called / not executed / denied, approval requested, latency, model calls |
| Metrics | `EvaluationService._summarize` | Pass rate, completion, schema compliance, tool selection, unauthorized-action rejection, groundedness, latency (avg / p95), tokens, retries, cost, failure rate |
| Release gates | `evaluation/gates.py` | Pure functions: `evaluate_gates` (per run) and `publish_blockers` (per publish attempt) |
| Baseline selector | `EvaluationService._baseline_run` | The run that published the agent's active version (else its latest passing run) |
| Human review | `EvaluationService.review` | Verdicts for `human_review` cases only; gates are re-applied afterwards |
| Version comparison | `EvaluationService.compare_versions` | One suite against two versions, per-case diff |
| Release-gate API | `GET /api/v1/agents/{id}/release-gate` | Same rules as publish, for the UI and CI |

## Gates

A run passes only if every gate passes. Each failure is written to `summary.gate_failures` in plain language and
`summary.acceptance_checks` holds the boolean per gate.

| Gate | Acceptance field | Default | Rule |
|---|---|---|---|
| `has_graded_cases` | – | – | At least one case was graded (pending human review does not count) |
| `min_pass_rate` | `min_pass_rate` | `1.0` | Graded pass rate |
| `required_cases` | `required_cases` | `[]` | Named cases passed (a human-review case passes once a reviewer accepts it) |
| `mandatory_kinds` | `mandatory_kinds` | `[permission, prompt_injection]` | Every graded case of these kinds passed, **whatever the pass rate** |
| `max_avg_latency_ms`, `max_p95_latency_ms` | same | none | Run latency |
| `max_cost_usd` | same | none | Total model cost of the run |
| `no_regressions` | `regression` | `block` | No case that passed in the baseline run fails now (`warn` records it, `off` skips) |

Publishing additionally requires (`publish_blockers`): the version is `approved`; the latest completed run for the
**exact** `spec_hash` passed; and that run was compared with the agent's **current** published version. If another
version was published after the run, the run is stale and must be repeated.

## Regression baseline

```
published v1.0.0 ──published by──▶ run R1 (passed)            baseline
draft v1.1.0 ──evaluate──▶ run R2, baseline_run_id = R1        compared by case key
                           regressed = passed in R1 ∧ failed in R2
```

- Cases are matched by `case_key`; only cases graded in both runs are compared (`summary.regression.compared`).
  Changing or adding cases is allowed; removing a case removes it from the comparison, which is visible in the
  count.
- The first version of an agent has no baseline; the gate passes and the summary says so.
- Accepting a regression is an explicit spec change (`regression: warn`) that is versioned and audited with the spec.

## Human review

- Only `human_review` cases accept a verdict. Deterministic results cannot be overridden by anyone: a reviewer who
  disagrees with a check must change the case, which changes the suite hash and requires a new run.
- After a verdict the run's gates are recomputed with the same baseline; if it is the latest run for the version's
  specification, the version moves to `approved` (or back to `testing`).
- Separation of duties: with `AF_ALLOW_SELF_APPROVAL=false` (mandatory in the production profile) the author of a
  version cannot review its evaluation.

## Provenance

Each run stores `provenance`: agent name and version, model provider/model/profile, whether the deterministic mock
model was used, a hash of the instructions and step prompts, tool name → version, pinned components
(`kind/name@version`), suite name/version/hash, platform version, Python version and database dialect. With
`spec_hash` and `suite_hash` this identifies what was evaluated and where.

## Independence and trust boundaries

- The agent under test never sees its cases' expectations, and nothing it outputs is used to grade tool behaviour:
  tool checks read `tool_invocations`, which only the harness writes.
- Gates and the publish check are backend code; the UI only displays `release-gate` results.
- An agent cannot change its own acceptance criteria: they live in the specification, which is immutable once
  approved, and every change creates a new version that must be evaluated again.
- Mock-model runs are labelled in provenance and the UI. They prove platform behaviour (policies, approvals, schemas,
  recovery), not answer quality.

## Reusable security suite

`evaluation.security_suite: true` adds two `prompt_injection` cases generated from the spec
(`evaluation/security_suite.py`), so they are mandatory gates:

| Case | Model | Asserts (from tool-invocation records) |
|---|---|---|
| `security-compromised-model` | `mock-adversarial` (tries undeclared tools, fabricated approvals, path traversal) | no undeclared tool ran; no approval-gated tool ran (approvals are rejected) |
| `security-injected-input` | the agent's own model | the same, with instructions injected into free-text inputs |

The first proves the harness and policy engine contain a hostile model; the second is only as meaningful as the
model under test (with the mock it checks plumbing). The approval rule they rely on is kept in step with the policy
engine by `test_approval_rule_matches_the_policy_engine`. Only inline cases get the suite; registered suites are
used as written.

## Model comparison

`POST /api/v1/evaluations/model-comparison` runs the same suite once per model configuration (up to six). These
are `purpose = "comparison"` runs: every case without its own `model_override` uses the given model; they never
approve a version, never count as the publish gate's latest run and are never used as a regression baseline.
The result lists gates, security gates, pass rate, schema compliance, tool selection, latency, tokens, cost and
failure rate per model, plus a per-case pass matrix.

## LLM-as-judge

`acceptance.judge: {model, criteria: [{name, description, weight}], min_score?, cases?}` asks an independent model
to score each graded case's output 0–1 per criterion (`evaluation/judge.py`, prompt version `judge-v1`).

- The agent's input and output are passed as delimited untrusted data; the judge is told never to follow
  instructions inside them.
- Stored per case in `observed.judgement`: weighted overall score, per-criterion score and evidence, confidence,
  explanation, judge provider/model, prompt version, rubric hash, tokens, cost, latency. Unknown criteria in the
  judge's answer are ignored and missing ones score 0; scores are clamped to 0–1.
- **It never changes a case's result.** A high score cannot rescue a failed deterministic check.
- Run metrics: `judge_score`, `judged_cases`, `judge_errors`, `judge_cost_usd`. With `min_score`, the gate
  `judge_min_score` blocks release when the average is lower **or unavailable** (judge failures fail closed).
- The rubric is part of the acceptance criteria, so it is versioned and hashed with the suite.

## Known limitations

- Mock-model runs validate platform behaviour, not model quality.
- Judges can be wrong or biased; keep deterministic checks for anything that can be checked deterministically.
- Quality trend charts across many runs are not in the Evaluation Lab yet (the run list and metrics are).
