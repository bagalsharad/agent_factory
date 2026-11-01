# ADR-0001: Modular monolith in a single Python package

Status: accepted (2026-10-30)

## Context
The build prompt suggests `apps/` + `packages/` with one package per concern. Python multi-package monorepos add
packaging, versioning and import-path overhead before there is any need to release modules independently.

## Decision
Ship one package, `agent_factory`, with sub-packages per concern (`spec`, `registry`, `builder`, `harness`,
`runtime`, `model_gateway`, `tools`, `memory`, `evaluation`, `governance`, `observability`, `storage`, `api`, `cli`).
Boundaries are enforced by constructor injection in `platform.py`; modules depend on interfaces (`ModelProvider`,
`Tool`, `Retriever`, `ArtifactStore`, `SecretStore`, `TaskDispatcher`, `Harness`), not on each other's internals.
The web UI lives in `web/`; the worker is the same image with `agent-factory worker`.

## Consequences
Simple install (`uv sync`), one lockfile, one test run. Splitting a plane into its own service later means
moving a sub-package behind an HTTP/queue boundary — the interfaces already exist.
