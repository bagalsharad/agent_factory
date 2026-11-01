# Roadmap

Near term
- MCP connector layer: explicit server trust, per-tool risk classification (default high + approval), allowlists.
- Embedding retriever (local Ollama embeddings) and pgvector adapter.
- LLM-as-judge evaluation signal (advisory, never the sole gate) and rubric-based human review UI.
- Workspace switcher and user/role management in the dashboard.
- Streaming model output to the execution monitor (SSE already used for events).

Mid term
- Object storage and external secret-manager adapters; Redis/queue dispatcher option.
- Kubernetes/Helm deployment profile with separate API/worker scaling and network policies.
- Policy-as-code export (e.g. OPA) for organization-wide governance.
- Cost accounting per workspace; budget alerts.

Long term
- Agent marketplace/registry federation with signed specifications.
- Continuous evaluation on production traffic samples with drift alerts.
