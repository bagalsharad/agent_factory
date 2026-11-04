# Configuration reference

All settings are environment variables prefixed with `AF_` (an optional `.env` file in the working directory is
read too). See [.env.example](../../.env.example) for an annotated template. Lists accept JSON (`["a","b"]`) or
comma-separated values. Secrets are never displayed by the API (`GET /api/v1/system/settings` reports
`{"configured": true}`).

Profiles: `AF_PROFILE=local` (default) and `production`. Production refuses to start without `api_key`/`oidc`
authentication and with self-approval enabled, and defaults content capture to off. Local auth mode refuses any
non-loopback `AF_HOST`.

Per-agent secrets for connectors are stored through the secret backend (`AF_SECRETS_BACKEND`): `env` reads
`AF_SECRET_<NAME>`; `keyring` uses the macOS Keychain; `file` uses a Fernet-encrypted file in the data directory.

<!-- generated -->
| Variable | Default | Description |
|---|---|---|
| `AF_PROFILE` | `"local"` |  |
| `AF_HOST` | `"127.0.0.1"` |  |
| `AF_PORT` | `8787` |  |
| `AF_DATA_DIR` | `data` |  |
| `AF_DATABASE_URL` | `null` |  |
| `AF_LOG_LEVEL` | `"INFO"` |  |
| `AF_LOG_JSON` | `true` |  |
| `AF_AUTH_MODE` | `"local"` |  |
| `AF_OIDC_ISSUER` | `null` |  |
| `AF_OIDC_AUDIENCE` | `null` |  |
| `AF_OIDC_JWKS_URL` | `null` |  |
| `AF_OIDC_ROLES_CLAIM` | `"af_roles"` |  |
| `AF_OIDC_DEFAULT_ROLE` | `"viewer"` |  |
| `AF_ALLOW_SELF_APPROVAL` | `true` |  |
| `AF_CORS_ORIGINS` | `["http://127.0.0.1:5173", "http://localhost:5173"]` |  |
| `AF_DEFAULT_PROVIDER` | `"mock"` |  |
| `AF_DEFAULT_MODEL` | `"mock-default"` |  |
| `AF_OLLAMA_BASE_URL` | `"http://127.0.0.1:11434"` |  |
| `AF_OLLAMA_ENABLED` | `true` |  |
| `AF_OPENAI_COMPAT_ENABLED` | `false` |  |
| `AF_OPENAI_COMPAT_BASE_URL` | `"http://127.0.0.1:1234/v1"` |  |
| `AF_OPENAI_COMPAT_API_KEY` | unset |  |
| `AF_CLOUD_PROVIDERS_ENABLED` | `false` |  |
| `AF_ANTHROPIC_API_KEY` | unset |  |
| `AF_ANTHROPIC_FALLBACKS` | `"default"` |  |
| `AF_MODEL_TIMEOUT_SECONDS` | `120.0` |  |
| `AF_MODEL_MAX_RETRIES` | `2` |  |
| `AF_WORKER_MODE` | `"embedded"` |  |
| `AF_WORKER_CONCURRENCY` | `2` |  |
| `AF_WORKER_POLL_INTERVAL_SECONDS` | `0.5` |  |
| `AF_WORKER_HEARTBEAT_STALE_SECONDS` | `60.0` |  |
| `AF_DEFAULT_MAX_STEPS` | `20` |  |
| `AF_DEFAULT_TIMEOUT_SECONDS` | `300` |  |
| `AF_APPROVAL_EXPIRY_SECONDS` | `86400` |  |
| `AF_MAX_REQUEST_BYTES` | `1000000` |  |
| `AF_EXECUTION_RATE_LIMIT_PER_MINUTE` | `60` |  |
| `AF_SAMPLE_DATA_DIR` | `examples` |  |
| `AF_KNOWLEDGE_DIR` | `data/knowledge` |  |
| `AF_SANDBOX_DIR` | `data/sandbox` |  |
| `AF_NETWORK_ALLOWLIST` | `[]` |  |
| `AF_ALLOW_PRIVATE_NETWORK` | `false` |  |
| `AF_SANDBOX_BACKEND` | `"none"` |  |
| `AF_SANDBOX_IMAGE` | `"python:3.12-slim"` |  |
| `AF_DISABLED_TOOLS` | `[]` |  |
| `AF_MCP_CONFIG_FILE` | `null` |  |
| `AF_DOCUMENT_DIRS` | `["~/Documents", "~/Downloads", "~/Desktop"]` | Folders the Interview Prep agent may read resumes, job descriptions and feedback from |
| `AF_INTERVIEW_PREP_DIR` | `~/Documents/InterviewPrep` | Where the Interview Prep agent writes `resume.docx`, `resume.pdf` and `prep-guide.md` |
| `AF_MAX_EXECUTION_ATTEMPTS` | `3` | Worker crashes tolerated per execution before it is dead-lettered for an operator |
| `AF_CIRCUIT_FAILURE_THRESHOLD` | `5` | Consecutive failures that open a model-provider or network/MCP tool circuit |
| `AF_CIRCUIT_RECOVERY_SECONDS` | `30.0` | How long a circuit stays open before one trial call |
| `AF_MCP_ALLOWED_COMMANDS` | `[]` | Executables stdio MCP servers may run (empty: stdio servers refused) |
| `AF_VAULT_ADDR` | `null` | HashiCorp Vault address for `AF_SECRETS_BACKEND=vault`; the token comes from `VAULT_TOKEN` |
| `AF_VAULT_MOUNT` | `"secret"` | Vault KV v2 mount |
| `AF_VAULT_PREFIX` | `"agent-factory"` | Path prefix inside the mount |
| `AF_AUDIT_SINK_PATH` | `null` | Append-only JSONL copy of every audit event (point at WORM/immutable storage) |
| `AF_CAPTURE_CONTENT` | `true` |  |
| `AF_TELEMETRY_EXPORTER` | `"none"` |  |
| `AF_OTLP_ENDPOINT` | `null` |  |
| `AF_RETENTION_DAYS` | `30` |  |
| `AF_SECRETS_BACKEND` | `"env"` | `env`, `keyring`, `file` or `vault` |
