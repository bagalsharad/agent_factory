# Model providers

| Provider | Enabled by default | Data leaves the machine | Configure |
|---|---|---|---|
| `mock` | yes | no | built in; models `mock-default`, `mock-adversarial`, `mock-failing`, `mock-flaky`, `mock-slow`, `mock-invalid-json`, `mock-no-tools` |
| `ollama` | yes (if reachable) | no | `AF_OLLAMA_BASE_URL` (default `http://127.0.0.1:11434`) |
| `openai_compatible` | no | only if the URL is not local (then also needs `AF_CLOUD_PROVIDERS_ENABLED=true`) | `AF_OPENAI_COMPAT_ENABLED=true`, `AF_OPENAI_COMPAT_BASE_URL`, `AF_OPENAI_COMPAT_API_KEY` |
| `anthropic` (Claude API) | no | yes | `uv sync --extra anthropic`, `AF_CLOUD_PROVIDERS_ENABLED=true`, `AF_ANTHROPIC_API_KEY`; default model `claude-opus-5-5` |

Capabilities are detected per model (Ollama's `/api/show`); when a model lacks native tool calling the harness uses
a JSON tool-calling protocol, and when it lacks structured output the schema is enforced by validation and retries.

**Never automatic downloads.** `agent-factory doctor` and *Models & Providers* recommend models for your RAM and
show the `ollama pull` command; you run it yourself.

LM Studio / vLLM / llama.cpp: start their OpenAI-compatible server, then
`AF_OPENAI_COMPAT_ENABLED=true AF_OPENAI_COMPAT_BASE_URL=http://127.0.0.1:1234/v1`.

Test a provider: `uv run agent-factory models test ollama qwen3:8b` or the *Test a model* card.
