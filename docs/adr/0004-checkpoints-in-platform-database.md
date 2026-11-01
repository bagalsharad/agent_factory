# ADR-0004: Checkpoints in the platform database

Status: accepted

Options: LangGraph's SQLite saver + Postgres saver packages, or a custom saver on our SQLAlchemy database. We
implemented `SqlCheckpointSaver` (full serialized checkpoint per row plus pending writes) so that SQLite and
PostgreSQL share one schema, one migration tool, one backup procedure and one retention policy. Trade-off: no blob
de-duplication (acceptable for bounded agent workflows) and we must track LangGraph's `BaseCheckpointSaver`
contract on upgrades (covered by `test_checkpoints_survive_process_restart`).
