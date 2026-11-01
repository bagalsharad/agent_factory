# Known limitations (0.1.0)

- **Mock-model results are not model-quality results.** The default evaluation suites pass with the deterministic
  mock model; they validate platform behaviour (tools, policies, approvals, schemas, recovery). Re-run suites with a
  real model (e.g. Ollama `qwen3:8b`) before trusting answer quality; small local models may fail schema checks.
- **Prompt injection is mitigated, not eliminated.** See the threat model.
- **MCP integration is not implemented.** Only registered built-in tools are available.
- **Write tools are simulated.** `restart_pipeline`, `update_pipeline_config`, `delete_dataset` record to a local
  sandbox log; real connectors must be implemented and reviewed.
- **Specialists in multi-agent workflows must be read-only**, and nested delegation is not allowed.
- **Child executions run inline** inside the parent's worker thread; the parent's timeout is checked between steps,
  not preemptively during a long child run.
- **Rate limiting is per process**; enforce global limits at the reverse proxy.
- **Retrieval is lexical (BM25)**; no embeddings by default.
- **Sandboxed code execution requires Docker** (`AF_SANDBOX_BACKEND=docker`); without it code tools are refused.
- **Single default workspace UI.** Multiple workspaces are supported by the data model and API (`X-Workspace-ID`);
  the dashboard has no workspace switcher beyond the Settings connection field.
- **Audit log is tamper-evident, not tamper-proof.**
- **Claude API adapter** has no live test in this repository.
- **SQLite** is intended for single-node local use; use PostgreSQL for multi-process production deployments.
