# Architecture decision records

| ADR | Decision |
|---|---|
| [0001](0001-modular-monolith-single-package.md) | Modular monolith in a single Python package instead of a multi-package monorepo |
| [0002](0002-declarative-specs-no-generated-code.md) | Declarative `agentfactory/v1` specifications; never execute model-generated code |
| [0003](0003-langgraph-orchestration.md) | LangGraph for workflow orchestration and human-in-the-loop |
| [0004](0004-checkpoints-in-platform-database.md) | Custom SQLAlchemy LangGraph checkpointer in the platform database |
| [0005](0005-database-task-queue.md) | Database-backed task queue; Redis optional later |
| [0006](0006-lexical-retrieval-first.md) | Local BM25 retrieval first; no vector database dependency |
| [0007](0007-policy-outside-the-model.md) | Authorization in a policy engine; approvals are database-authoritative |
| [0008](0008-deterministic-mock-provider.md) | Deterministic mock model provider for tests, demos and CI |
| [0009](0009-spa-served-by-api.md) | React SPA built with Vite and served by the API |
| [0010](0010-optional-cloud-providers.md) | Cloud model providers are optional adapters, disabled by default |
