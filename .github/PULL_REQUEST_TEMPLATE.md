## Summary

<!-- What does this change and why? Link issues. -->

## Checklist

- [ ] `make lint typecheck test` passes locally
- [ ] New behaviour is covered by tests (state whether they use the mock model or real inference)
- [ ] Database changes include an Alembic migration (`alembic revision --autogenerate`) and `alembic check` passes
- [ ] Security review: permissions, untrusted input handling, secret exposure, error handling, execution boundaries
- [ ] Docs updated (README, docs/, ADR for architectural decisions, CHANGELOG)
- [ ] No secrets, local databases, execution logs or model files committed
