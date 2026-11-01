# Troubleshooting

| Symptom | Fix |
|---|---|
| `refusing to start: AF_AUTH_MODE=local ... must bind to a loopback address` | Keep `AF_HOST=127.0.0.1`, or use `AF_AUTH_MODE=api_key`/`oidc` for network exposure |
| Dashboard shows a JSON message instead of the UI | Build it: `make web` |
| Ollama `down` | `ollama serve`; check `AF_OLLAMA_BASE_URL`; `agent-factory doctor` |
| `Model 'x' is not installed in Ollama` | `ollama pull x` (never automatic) |
| Execution stuck in `queued` | The worker is not running: embedded mode needs `agent-factory serve`; external mode needs `agent-factory worker`. Check `/ready` |
| `waiting_approval` forever | Decide it in *Approvals* or `agent-factory approvals list`; approvals expire after 24 h by default |
| `budget_exceeded` | Raise `spec.execution_limits` (new version) — check the timeline for which limit tripped |
| `output_validation_failed` | The model's output didn't match `spec.outputs`; inspect `validation.failed` events; simplify the schema or use a stronger model |
| `Agent has no executable version` | Validate and build the agent; drafts never execute |
| Publish refused | The latest evaluation for that exact spec must pass its acceptance criteria |
| `401` in the dashboard | Server runs with `api_key`/`oidc`: paste an API key in *Settings → Connection* |
| Port in use | `AF_PORT=8790 agent-factory serve` |
| Reset local state | Stop the server, back up if needed (`agent-factory db backup`), delete `data/`, run `agent-factory init` |

Include the `X-Request-ID` from a failing response when filing a bug; it correlates logs, events and audit entries.
