# Data Pipeline Diagnostic Agent

Analyzes failed pipeline runs from **approved sample** logs and metadata (`data/pipelines/`) and recommends
corrective actions. Read-only; no network; no production connections.

- Workflow: `collect_evidence` → `inspect_metadata` → `analyze_failure` (structured model step) → `validate_findings`
- Tools: `read_pipeline_logs`, `inspect_execution_metadata` (path-guarded, ID-validated)
- Components: evidence-based and untrusted-content instructions, `non-empty-recommendations` rule, `diagnose-failure` prompt
- Sample data: `orders_daily` (success, schema mismatch, upstream timeout), `customer_sync` (expired credentials,
  OOM), `events_stream` (dead letters + a planted prompt injection)
- Evaluation: 13 cases (functional, regression, injection ×2, recovery, outage, timeout, malformed output,
  input validation, missing data, human review); acceptance 100% with required injection cases

```bash
uv run agent-factory agents run data-pipeline-diagnostic -i @examples/data-pipeline-diagnostic/sample_inputs/schema_mismatch.json
uv run agent-factory evaluate data-pipeline-diagnostic
```
