# Contributing to Agent Factory

Thanks for helping! This project values safety, reproducibility and clarity over feature count.

## Workflow

1. Open an issue for anything non-trivial (design discussion first for new tools, permissions or data flows).
2. Fork, branch from `main`, keep changes focused.
3. `make install && uv run pre-commit install`
4. Make the change with tests; run `make lint typecheck test`.
5. Database changes: add an Alembic migration and make sure `uv run alembic check` passes.
6. Architectural decisions: add an ADR in `docs/adr/`. User-visible changes: update docs and `CHANGELOG.md`.
7. Open a pull request using the template. CI must pass.

## Standards

- Python 3.11+, typed (`mypy` clean), formatted with `ruff format`, linted with `ruff check`.
- TypeScript strict, ESLint clean with zero warnings.
- Tests state whether they use the deterministic mock model or real inference. Never mark skipped tests as passed.
- Security review for every feature: permissions, untrusted input, secret exposure, error handling, execution
  boundaries. Tools are trusted code — new tools need explicit risk/side-effect classification and guard tests.
- Never commit secrets, local databases, execution logs or model files.
- Use semantic versioning; breaking changes to the spec format need an upgrader in `spec/loader.py`.

## Support boundaries

This is community-supported, pre-1.0 software. Issues are triaged best-effort. Security reports follow
[SECURITY.md](SECURITY.md).

By contributing you agree that your contributions are licensed under the Apache License 2.0.
