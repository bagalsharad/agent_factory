# Agent specification reference (`agentfactory/v1`)

Machine-readable schema: [agent-spec.schema.json](agent-spec.schema.json) (also `GET /api/v1/schemas/agent-spec`).
A specification is data: no code, no credentials. Status, ids and timestamps belong to the version record.

```yaml
api_version: agentfactory/v1
kind: Agent
metadata:
  name: data-pipeline-diagnostic        # slug, unique per workspace (immutable)
  display_name: Data Pipeline Diagnostic Agent
  version: 1.0.0                        # semver; edits to approved/published versions create a new version
  description: ...
  labels: {domain: data-engineering}
  template: data-pipeline-diagnostic@1.0.0
spec:
  objective: ...                        # 8–4000 chars
  instructions: ...                     # system prompt
  model:
    provider: ollama                    # mock | ollama | openai_compatible | anthropic
    model: qwen3:8b
    profile: null                       # optional registered model profile
    temperature: 0
    max_output_tokens: 2048
    required_capabilities: [chat, structured_output]   # chat|tools|structured_output|streaming|vision|embeddings
    timeout_seconds: 120
  inputs:  {type: object, required: [...], properties: {...}}    # JSON Schema; validated before queueing
  outputs: {type: object, required: [...], properties: {...}}    # JSON Schema; final output must conform
  tools: [read_pipeline_logs, inspect_execution_metadata@1.0.0]  # registered tools only
  components:                           # pinned, immutable component versions
    - {kind: system_instruction, name: evidence-based-analyst, version: 1.0.0}
  workflow: {...}                       # see below
  memory:
    enabled: false                      # shorthand: disables all memory
    conversation: {enabled: false, max_messages: 20}
    agent: {enabled: false, max_items: 50}
    knowledge: {enabled: false, collections: [], top_k: 5, min_score: 0.0}
  execution_limits:
    max_steps: 20
    max_model_calls: 20
    max_tool_calls: 30
    timeout_seconds: 300                # active time; approval waiting time excluded
    max_retries: 2                      # model retries, idempotent tool retries, output re-asks
    max_context_tokens: 16000
    max_output_tokens: 2048
    max_cost_usd: null
    max_subagents: 4
  policies:
    version: "1"
    external_network: false
    allowed_network_hosts: []           # must ALSO be in AF_NETWORK_ALLOWLIST
    allow_write_operations: false
    require_approval_for: [production_restart, configuration_change, data_deletion]
    data_classification: internal       # public | internal | confidential | restricted
    capture_content: null               # override platform content capture
  human_approval:
    required_for_risk: [high, critical]
    require_for_writes: true
    expires_after_seconds: 86400
    approver_roles: [admin, operator]
  evaluation:
    suite: null                         # name of a registered suite, or inline cases:
    cases: [...]
    acceptance: {min_pass_rate: 1.0, required_cases: [], max_avg_latency_ms: null}
```

## Workflow

`type`: `single` | `graph` (linear steps) | `sequential` (delegate steps) | `supervisor` (agent loop + specialists).

| Step kind | Fields | Behaviour |
|---|---|---|
| `model` | `prompt`, `structured` | One model call (+ validation re-asks) |
| `agent_loop` | `prompt`, `structured`, `tools` (subset), `max_iterations` | Model ⇄ tools cycle; policy-filtered tools; supervisor adds `delegate_to_<id>` |
| `tool` | `tool`, `arguments` (templated) | Deterministic tool call through the policy engine |
| `validate` | `target` | Validates a prior step's output against `outputs` + validation-rule components |
| `approval` | `action`, `rationale` | Explicit human checkpoint; rejection halts the workflow (`rejected`) |
| `delegate` | `agent` (`name[@version]`), `task` | Runs a specialist as a child execution |
| `retrieve` | `query`, `top_k`, `collections` | Knowledge retrieval (requires `memory.knowledge`) |

Common fields: `id` (snake_case), `description`, `final` (one step), `on_error: fail|continue`.
Templates: `{{ input.<path> }}`, `{{ steps.<id>.output[.<path>] }}`, `{{ components.<prompt_template_name> }}`.
A lone placeholder keeps the value's type (lists, numbers).

`specialists: [{id, agent, description}]`, `max_delegations`. Specialists must be built, single-agent and read-only.

## Evaluation cases

```yaml
- id: injection-adversarial-model
  name: A compromised model still cannot act on injected instructions
  kind: prompt_injection        # functional|input_validation|output_schema|tool_selection|tool_invocation|permission|
                                # prompt_injection|workflow_recovery|timeout|model_failure|regression|performance|human_review
  input: {...}
  model_override: {provider: mock, model: mock-adversarial}
  approval_decision: none       # approve | reject | none (simulated reviewer in evaluation mode only)
  limits_override: {timeout_seconds: 2}
  fault_injection: {fail_tool_once: read_pipeline_logs}   # or fail_tool_always
  expected:
    status: completed           # or failed | budget_exceeded | rejected | waiting_approval | rejected_input
    output_schema_valid: true
    output_contains: [...]
    output_not_contains: [...]
    output_fields: {probable_causes: {min_items: 1}, severity: {equals: high}}
    tools_called: [...]
    tools_not_called: [...]
    tools_denied: [...]
    approval_requested: false
    max_latency_ms: 10000
    max_model_calls: 5
    error_code: provider_error
```

## Validation

Beyond the schema, the validator checks: registered/enabled tools, provider known/enabled, capabilities, JSON Schema
validity, write/network policy consistency, allowlists, embedded credentials, workflow wiring and template
references, delegation rules (exists, executable, not nested, read-only, no self-reference), knowledge
configuration, limits vs workflow size, and evaluation inputs.

## Schema evolution

`api_version` selects upgraders (`spec/loader.py`). Documents are upgraded on load and stored in the current
version. The legacy prototype format (`schema_version: "1.0"`) is upgraded automatically.
