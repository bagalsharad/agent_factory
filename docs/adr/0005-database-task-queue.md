# ADR-0005: Database-backed task queue

Status: accepted

Executions are queued as rows (`status='queued'`) and claimed with a compare-and-set `UPDATE ... WHERE
status='queued'`, which is safe across processes on SQLite and PostgreSQL. The same `DatabaseQueueDispatcher` runs
as embedded threads locally and as `agent-factory worker` containers in production. Workers heartbeat; stale
`running` executions are re-queued and resume from their checkpoint. Redis is not required. A queue-backed
dispatcher can implement `TaskDispatcher` when throughput demands it.
