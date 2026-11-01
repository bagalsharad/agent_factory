# CLI reference

`uv run agent-factory --help` lists everything. The CLI uses the same services as the API and works without a
running server (it acts as the local administrator, or as `AF_CLI_SUBJECT`).

| Command | Purpose |
|---|---|
| `doctor [--json]` | Preflight checks and model recommendations |
| `init [--no-examples] [--no-evaluate]` | Migrate, seed templates/components, install (and evaluate/publish) examples |
| `serve [--host] [--port]` | API + dashboard |
| `worker [--concurrency N]` | External execution worker (`AF_WORKER_MODE=external`) |
| `status [--json]` | Database, migrations, providers, counts, audit chain |
| `models list`, `models test PROVIDER MODEL` | Providers and models |
| `agents list`, `agents show NAME` | Catalog |
| `agents create (--file F | --objective "..." | --template T --name N) [--no-build]` | Create (and build) agents |
| `agents build NAME [--version V]`, `agents publish NAME` | Lifecycle |
| `agents run NAME -i JSON|@file [--version V] [--json]` | Execute synchronously with timeline |
| `evaluate NAME [--version V] [--json]` | Run the evaluation suite (exit code 1 if it fails) |
| `approvals list [--status S]`, `approvals approve ID`, `approvals reject ID` | Human approvals |
| `examples install` | Install bundled examples |
| `db migrate`, `db backup [-o FILE]`, `db restore FILE --yes` | Database operations |
| `users create SUBJECT --role ROLE [--email]` | Users and API keys (shown once) |
| `audit-verify` | Verify the audit hash chain |
| `purge --older-than-days N` | Retention purge |
