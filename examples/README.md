# Example agents

Installed by `agent-factory init` (or `agent-factory examples install`) in the order given by `manifest.yaml`.

| Directory | Agent(s) | Pattern |
|---|---|---|
| `data-pipeline-diagnostic/` | `data-pipeline-diagnostic` | single agent, deterministic graph |
| `document-research/` | `document-research` | agent loop with retrieval |
| `software-review/` | 3 specialists + `software-review-coordinator` | supervisor + specialists |
| `pipeline-remediation/` | `pipeline-remediation` | human-in-the-loop approval |

Each example contains the specification (`agent.yaml`) with instructions, workflow, tools and its evaluation suite,
sample inputs, expected outputs (key facts) and sample data. All data is synthetic; nothing connects to real systems.
See [docs/user-guide/walkthrough.md](../docs/user-guide/walkthrough.md).
