# Pipeline Remediation Agent (human-in-the-loop)

Inspects a failed run and requests `restart_pipeline` (action class `production_restart`, high risk). The workflow
pauses, persists its checkpoint and waits for an authorized approver (`admin`/`operator`). Approval resumes and
records a **simulated** restart once in `data/sandbox/simulated_actions.jsonl`; rejection resumes without it.

```bash
uv run agent-factory agents run pipeline-remediation -i @examples/pipeline-remediation/sample_inputs/orders_timeout.json
uv run agent-factory approvals list && uv run agent-factory approvals approve <id>
```
