"""Conversation memory (per session) and agent memory (per agent key/value facts)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from ..core.errors import NotFoundError
from ..storage.db import Database
from ..storage.models import AgentMemoryRecord, ConversationMessage, ConversationSession


class ConversationMemory:
    def __init__(self, db: Database) -> None:
        self.db = db

    def ensure_session(self, workspace_id: str, agent_id: str, session_id: str | None, user_id: str | None) -> str:
        with self.db.transaction() as session:
            if session_id:
                existing = session.get(ConversationSession, session_id)
                if not existing or existing.workspace_id != workspace_id or existing.agent_id != agent_id:
                    raise NotFoundError("Conversation session not found")
                return existing.id
            record = ConversationSession(workspace_id=workspace_id, agent_id=agent_id, user_id=user_id)
            session.add(record)
            session.flush()
            return record.id

    def history(self, session_id: str, max_messages: int) -> list[dict[str, str]]:
        with self.db.session() as session:
            rows = session.scalars(
                select(ConversationMessage)
                .where(ConversationMessage.session_id == session_id)
                .order_by(ConversationMessage.created_at.desc())
                .limit(max_messages)
            ).all()
        return [{"role": r.role, "content": r.content} for r in reversed(rows)]

    def append(self, session_id: str, role: str, content: str, execution_id: str | None = None) -> None:
        with self.db.transaction() as session:
            session.add(
                ConversationMessage(session_id=session_id, role=role, content=content, execution_id=execution_id)
            )


class AgentMemory:
    def __init__(self, db: Database) -> None:
        self.db = db

    def items(self, agent_id: str, limit: int) -> list[dict[str, Any]]:
        with self.db.session() as session:
            rows = session.scalars(
                select(AgentMemoryRecord)
                .where(AgentMemoryRecord.agent_id == agent_id)
                .order_by(AgentMemoryRecord.updated_at.desc())
                .limit(limit)
            ).all()
        return [{"key": r.key, "value": r.value} for r in rows]

    def put(
        self, workspace_id: str, agent_id: str, key: str, value: dict[str, Any], execution_id: str | None = None
    ) -> None:
        with self.db.transaction() as session:
            row = session.scalars(
                select(AgentMemoryRecord).where(AgentMemoryRecord.agent_id == agent_id, AgentMemoryRecord.key == key)
            ).first()
            if row:
                row.value, row.source_execution_id = value, execution_id
            else:
                session.add(
                    AgentMemoryRecord(
                        workspace_id=workspace_id,
                        agent_id=agent_id,
                        key=key,
                        value=value,
                        source_execution_id=execution_id,
                    )
                )
