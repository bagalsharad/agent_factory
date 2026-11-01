# Getting started

## 1. Install

```bash
make install                     # uv sync --frozen --all-extras && npm ci (web)
uv run agent-factory doctor      # preflight report + model recommendations for this Mac
make web                         # build the dashboard (or skip and use only the API/CLI)
uv run agent-factory init        # database, templates, example agents (evaluated and published)
uv run agent-factory serve       # http://127.0.0.1:8787
```

`init` installs four example agents, ingests the sample document corpus, runs every example's evaluation suite and
publishes the agents whose suites pass. Use `agent-factory init --no-evaluate` to skip evaluation.

## 2. Look around the dashboard

- **Dashboard** — agent counts, active/failed executions, provider status, runtime health.
- **Models & Providers** — confirm `mock` is up; Ollama shows `down` until you start it.
- **Agent Catalog** — the example agents with their lifecycle status.

## 3. Execute an agent

Open *Data Pipeline Diagnostic Agent* → **Execute**, pick the sample "Diagnoses a schema mismatch", **Run**. The
execution monitor shows the workflow graph lighting up node by node, the event timeline, tool calls with their
policy decisions, the final structured output and the configuration snapshot.

## 4. Create your own agent

**Agent Designer** → describe the agent ("Create an agent that…") → *Generate draft specification*. Review each
step (model, tools, workflow, permissions & budgets) or switch to **Advanced YAML**. *Save & build* creates a draft
version, validates it and builds it. Add evaluation cases (YAML `spec.evaluation.cases`), run them from the agent
page, and publish once the acceptance criteria pass.

## 5. Use a real local model

```bash
ollama serve
ollama pull qwen3:8b             # choose from `agent-factory doctor`
```

Edit the agent's model (designer step 3) to provider `ollama`, model `qwen3:8b`, build, evaluate. See
[model providers](model-providers.md).
