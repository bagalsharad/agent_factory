# Local Agent Factory

A local-first control plane for the lifecycle of AI agents: describe an objective, create a validated specification, run it, evaluate repeatable tests, and promote a version for use.

## What is implemented

- **Agent Designer:** objective, constraints, approved capabilities, model provider, and human approval gates.
- **Schema-first Builder:** creates a structured agent definition rather than executable code.
- **Registry and Versioning:** each design starts as a `v0.1.0` draft stored in SQLite.
- **Testing Lab:** save repeatable prompts and transparent phrase-based acceptance checks.
- **Runtime:** demo mode, Ollama, and OpenAI-compatible local endpoints.
- **Promotion:** explicitly approve one version as the active registry definition.
- **Local dashboard and API:** dashboard at `/`; interactive API specification at `/docs`.

## Start

```bash
cd /Users/sharadbagal/gen_ai_apps/agent_factory
python3 app.py
```

Open [http://127.0.0.1:8787](http://127.0.0.1:8787) or [http://127.0.0.1:8787/docs](http://127.0.0.1:8787/docs). The server binds to loopback only, and state is stored in `data/agent_factory.db`.

## Use a local model

Install Ollama, pull a model suitable for your Mac (for example `ollama pull qwen3:8b`), then choose **Ollama (local)** in the Agent Designer and set the model name. No task data leaves the Mac through that path.

An OpenAI-compatible local service, such as LM Studio, can be used by setting `AGENT_FACTORY_API_BASE` and optional `AGENT_FACTORY_API_KEY` before starting the app.

## Safety model

This is deliberately not an arbitrary-code generator. Tools are allowlisted and read-only in the current prototype. Destructive or external operations stay as proposed approval gates; the runtime does not execute them. A future LangGraph orchestration layer can attach implementations only after their contracts and approval behavior are defined.
