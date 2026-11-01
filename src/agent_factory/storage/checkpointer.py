"""LangGraph checkpoint saver backed by the platform's relational database.

Why a custom saver (ADR-0004): the platform already runs SQLAlchemy + Alembic on SQLite (local) and
PostgreSQL (production). Storing checkpoints in the same database keeps one backup/restore procedure,
one migration system and one retention policy, and avoids a second persistence dependency per backend.

The implementation stores the full serialized checkpoint (including channel values) per row. That is
simpler than blob de-duplication and adequate for bounded agent workflows.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import (
    WRITES_IDX_MAP,
    BaseCheckpointSaver,
    ChannelVersions,
    Checkpoint,
    CheckpointMetadata,
    CheckpointTuple,
    get_checkpoint_id,
    get_checkpoint_metadata,
    writes_sort_key,
)
from sqlalchemy import delete, select

from .db import Database
from .models import CheckpointRecord, CheckpointWriteRecord


class SqlCheckpointSaver(BaseCheckpointSaver[int]):
    def __init__(self, database: Database) -> None:
        super().__init__()
        self.db = database

    # ------------------------------------------------------------------ helpers
    def _writes(self, session: Any, thread_id: str, ns: str, checkpoint_id: str) -> list[tuple[str, str, Any]]:
        rows = session.scalars(
            select(CheckpointWriteRecord).where(
                CheckpointWriteRecord.thread_id == thread_id,
                CheckpointWriteRecord.checkpoint_ns == ns,
                CheckpointWriteRecord.checkpoint_id == checkpoint_id,
            )
        ).all()
        rows = sorted(rows, key=lambda r: writes_sort_key(r.task_path, r.task_id, r.idx))
        return [(r.task_id, r.channel, self.serde.loads_typed((r.value_type, r.value))) for r in rows]

    def _to_tuple(self, session: Any, row: CheckpointRecord) -> CheckpointTuple:
        checkpoint: Checkpoint = self.serde.loads_typed((row.checkpoint_type, row.checkpoint))
        metadata: CheckpointMetadata = self.serde.loads_typed((row.metadata_type, row.metadata_))
        return CheckpointTuple(
            config={
                "configurable": {
                    "thread_id": row.thread_id,
                    "checkpoint_ns": row.checkpoint_ns,
                    "checkpoint_id": row.checkpoint_id,
                }
            },
            checkpoint=checkpoint,
            metadata=metadata,
            parent_config=(
                {
                    "configurable": {
                        "thread_id": row.thread_id,
                        "checkpoint_ns": row.checkpoint_ns,
                        "checkpoint_id": row.parent_checkpoint_id,
                    }
                }
                if row.parent_checkpoint_id
                else None
            ),
            pending_writes=self._writes(session, row.thread_id, row.checkpoint_ns, row.checkpoint_id),
        )

    # ------------------------------------------------------------------ sync API
    def get_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        with self.db.session() as session:
            query = select(CheckpointRecord).where(
                CheckpointRecord.thread_id == thread_id, CheckpointRecord.checkpoint_ns == ns
            )
            if checkpoint_id := get_checkpoint_id(config):
                query = query.where(CheckpointRecord.checkpoint_id == checkpoint_id)
            else:
                query = query.order_by(CheckpointRecord.checkpoint_id.desc()).limit(1)
            row = session.scalars(query).first()
            return self._to_tuple(session, row) if row else None

    def list(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> Iterator[CheckpointTuple]:
        with self.db.session() as session:
            query = select(CheckpointRecord)
            if config:
                query = query.where(CheckpointRecord.thread_id == config["configurable"]["thread_id"])
                ns = config["configurable"].get("checkpoint_ns")
                if ns is not None:
                    query = query.where(CheckpointRecord.checkpoint_ns == ns)
                if checkpoint_id := get_checkpoint_id(config):
                    query = query.where(CheckpointRecord.checkpoint_id == checkpoint_id)
            if before and (before_id := get_checkpoint_id(before)):
                query = query.where(CheckpointRecord.checkpoint_id < before_id)
            query = query.order_by(CheckpointRecord.checkpoint_id.desc())
            remaining = limit
            for row in session.scalars(query):
                tup = self._to_tuple(session, row)
                if filter and not all(tup.metadata.get(k) == v for k, v in filter.items()):
                    continue
                if remaining is not None:
                    if remaining <= 0:
                        break
                    remaining -= 1
                yield tup

    def put(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        ctype, cbytes = self.serde.dumps_typed(checkpoint)
        mtype, mbytes = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        with self.db.transaction() as session:
            existing = session.scalars(
                select(CheckpointRecord).where(
                    CheckpointRecord.thread_id == thread_id,
                    CheckpointRecord.checkpoint_ns == ns,
                    CheckpointRecord.checkpoint_id == checkpoint["id"],
                )
            ).first()
            if existing:
                existing.checkpoint_type, existing.checkpoint = ctype, cbytes
                existing.metadata_type, existing.metadata_ = mtype, mbytes
            else:
                session.add(
                    CheckpointRecord(
                        thread_id=thread_id,
                        checkpoint_ns=ns,
                        checkpoint_id=checkpoint["id"],
                        parent_checkpoint_id=config["configurable"].get("checkpoint_id"),
                        checkpoint_type=ctype,
                        checkpoint=cbytes,
                        metadata_type=mtype,
                        metadata_=mbytes,
                    )
                )
        return {"configurable": {"thread_id": thread_id, "checkpoint_ns": ns, "checkpoint_id": checkpoint["id"]}}

    def put_writes(
        self,
        config: RunnableConfig,
        writes: Sequence[tuple[str, Any]],
        task_id: str,
        task_path: str = "",
    ) -> None:
        thread_id = config["configurable"]["thread_id"]
        ns = config["configurable"].get("checkpoint_ns", "")
        checkpoint_id = config["configurable"]["checkpoint_id"]
        with self.db.transaction() as session:
            for position, (channel, value) in enumerate(writes):
                idx = WRITES_IDX_MAP.get(channel, position)
                existing = session.scalars(
                    select(CheckpointWriteRecord).where(
                        CheckpointWriteRecord.thread_id == thread_id,
                        CheckpointWriteRecord.checkpoint_ns == ns,
                        CheckpointWriteRecord.checkpoint_id == checkpoint_id,
                        CheckpointWriteRecord.task_id == task_id,
                        CheckpointWriteRecord.idx == idx,
                    )
                ).first()
                vtype, vbytes = self.serde.dumps_typed(value)
                if existing:
                    if idx >= 0:
                        continue  # regular writes are write-once; special writes (negative idx) upsert
                    existing.channel, existing.value_type, existing.value = channel, vtype, vbytes
                    continue
                session.add(
                    CheckpointWriteRecord(
                        thread_id=thread_id,
                        checkpoint_ns=ns,
                        checkpoint_id=checkpoint_id,
                        task_id=task_id,
                        task_path=task_path,
                        idx=idx,
                        channel=channel,
                        value_type=vtype,
                        value=vbytes,
                    )
                )

    def delete_thread(self, thread_id: str) -> None:
        with self.db.transaction() as session:
            session.execute(delete(CheckpointWriteRecord).where(CheckpointWriteRecord.thread_id == thread_id))
            session.execute(delete(CheckpointRecord).where(CheckpointRecord.thread_id == thread_id))

    # ------------------------------------------------------------------ async API (delegates)
    async def aget_tuple(self, config: RunnableConfig) -> CheckpointTuple | None:
        return self.get_tuple(config)

    async def alist(
        self,
        config: RunnableConfig | None,
        *,
        filter: dict[str, Any] | None = None,
        before: RunnableConfig | None = None,
        limit: int | None = None,
    ) -> AsyncIterator[CheckpointTuple]:
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(
        self,
        config: RunnableConfig,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions: ChannelVersions,
    ) -> RunnableConfig:
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(
        self, config: RunnableConfig, writes: Sequence[tuple[str, Any]], task_id: str, task_path: str = ""
    ) -> None:
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str) -> None:
        self.delete_thread(thread_id)
