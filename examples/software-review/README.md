# Multi-Agent Software Review Workflow

Supervisor (`software-review-coordinator`, the *Final Review Coordinator*) + three read-only specialists
(`agents/architecture-reviewer.yaml`, `security-reviewer.yaml`, `code-quality-reviewer.yaml`). The specialists list,
read and statically scan `samples/payments-service/` — a deliberately flawed artifact. **Reviewed code is never
executed.** Delegation is bounded (`max_delegations: 4`, `max_subagents: 4`) and specialists cannot delegate.

```bash
uv run agent-factory agents run software-review-coordinator -i @examples/software-review/sample_inputs/payments_service.json
```
