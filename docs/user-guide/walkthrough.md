# Example walkthroughs

All commands assume `uv run agent-factory init` has been run. Every example also works from the dashboard.

## Data Pipeline Diagnostic Agent (single agent, graph workflow)

```bash
uv run agent-factory agents run data-pipeline-diagnostic -i @examples/data-pipeline-diagnostic/sample_inputs/schema_mismatch.json
```

Workflow: `collect_evidence` (read_pipeline_logs) → `inspect_metadata` → `analyze_failure` (model, structured) →
`validate_findings` (schema + `non-empty-recommendations` rule). Expected key facts are in
`examples/data-pipeline-diagnostic/expected_outputs/`. The 13-case suite (`agent-factory evaluate
data-pipeline-diagnostic`) covers regressions, an injected log line, a compromised model, a transient tool failure
(retried), a provider outage, a timeout, malformed output, a path-traversal input and a human-reviewed case.

## Document Research Agent (agent loop + retrieval)

```bash
uv run agent-factory agents run document-research -i '{"question": "What is the freshness SLA for gold tier pipelines?"}'
```

The agent searches the `research-docs` collection (ingested from `examples/document-research/documents/`), reads
documents and answers with references; unsupported parts are listed under `unsupported_conclusions`.
`vendor-security-review.md` contains a planted prompt injection; the `injected-document-adversarial` case shows the
policy engine denying the `delete_dataset` call a compromised model attempts.

## Multi-Agent Software Review (supervisor + specialists)

```bash
uv run agent-factory agents run software-review-coordinator -i '{"artifact": "payments-service"}'
```

The coordinator delegates to `architecture-reviewer`, `security-reviewer` and `code-quality-reviewer` (each a
separately built, read-only agent), receives structured findings and produces a single verdict. The sample
artifact (`examples/software-review/samples/payments-service/`) contains planted SQL injection, a hard-coded
credential, `eval`, pickle deserialization, disabled TLS verification and swallowed exceptions. Reviewed code is
only read and pattern-scanned — never executed.

## Pipeline Remediation Agent (human in the loop)

```bash
uv run agent-factory agents run pipeline-remediation -i @examples/pipeline-remediation/sample_inputs/orders_timeout.json
uv run agent-factory approvals list
uv run agent-factory approvals approve <approval-id> --comment "transient upstream timeout"
```

The run pauses at `request_restart` (`restart_pipeline`, action class `production_restart`, risk high). Approving
resumes the persisted workflow; the simulated restart is recorded exactly once in `data/sandbox/simulated_actions.jsonl`.
Rejecting resumes without performing it, and the report says so.
