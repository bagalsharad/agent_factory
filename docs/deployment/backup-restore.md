# Backup and restore

What to back up: the relational database (agents, versions, executions, checkpoints, approvals, evaluations,
audit, knowledge metadata) and the artifact store (`$AF_DATA_DIR/artifacts`: uploaded/ingested documents). Secrets
live in your secret backend and are backed up there.

## Local (SQLite)

```bash
uv run agent-factory db backup                    # online backup → data/backups/agent_factory-<ts>.db (+ artifacts tar.gz)
uv run agent-factory db restore data/backups/agent_factory-20261030-120000.db --yes   # stop the server first
uv run agent-factory db migrate                   # if the backup is from an older version
```

## Production (PostgreSQL in Compose)

```bash
# backup (daily via cron; keep off-host copies)
docker compose --env-file deploy/compose/production.env exec -T postgres \
  pg_dump -U agentfactory -d agentfactory --format=custom > agent_factory-$(date +%F).dump
docker run --rm -v agent-factory_app-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/artifacts-$(date +%F).tar.gz -C /data artifacts

# restore
docker compose --env-file deploy/compose/production.env stop api worker
docker compose --env-file deploy/compose/production.env exec -T postgres \
  pg_restore -U agentfactory -d agentfactory --clean --if-exists < agent_factory-2026-10-30.dump
docker run --rm -v agent-factory_app-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/artifacts-2026-10-30.tar.gz -C /data
docker compose --env-file deploy/compose/production.env start api worker
curl -fsS https://$AF_DOMAIN/ready && uv run agent-factory audit-verify   # or GET /api/v1/audit/verify
```

Verify restores regularly in a staging environment. After restore, executions that were `running` are re-queued
automatically by the worker's stale-heartbeat recovery and resume from their last checkpoint.

## Disaster recovery assumptions

RPO equals the backup interval (use PostgreSQL PITR/WAL archiving or a managed database for lower RPO). RTO is the
time to provision a host, restore, and start the stack (typically under an hour for the single-node design).
