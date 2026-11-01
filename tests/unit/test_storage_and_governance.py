"""Audit hash chain, checkpointer durability, knowledge retrieval, components and templates."""

from __future__ import annotations

import operator
from pathlib import Path
from typing import Annotated, TypedDict

import pytest
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from sqlalchemy import update

from agent_factory.core.errors import ConflictError, InvalidStateError, NotFoundError, ValidationFailedError
from agent_factory.governance.rbac import Principal
from agent_factory.memory.knowledge import chunk_text, extract_text
from agent_factory.platform import Platform
from agent_factory.registry.components import ComponentCreate
from agent_factory.storage.checkpointer import SqlCheckpointSaver
from agent_factory.storage.db import Database
from agent_factory.storage.models import AuditEvent
from tests.conftest import EXAMPLES, add_user


def test_audit_chain_detects_tampering(platform: Platform) -> None:
    for i in range(3):
        platform.audit.record("test.event", "thing", str(i), details={"i": i, "password": "secret-value"})
    assert platform.audit.verify()["valid"]
    with platform.db.transaction() as session:
        session.execute(update(AuditEvent).where(AuditEvent.action == "test.event", AuditEvent.resource_id == "1")
                        .values(outcome="tampered"))
    result = platform.audit.verify()
    assert not result["valid"] and result["broken_at_seq"] > 0


def test_audit_details_are_redacted(platform: Platform) -> None:
    platform.audit.record("test.redact", "thing", "x", details={"password": "hunter2", "note": "Bearer abc.def"})
    with platform.db.session() as session:
        event = session.query(AuditEvent).filter(AuditEvent.action == "test.redact").one()
    assert "hunter2" not in str(event.details) and "abc.def" not in str(event.details)


class S(TypedDict):
    log: Annotated[list, operator.add]


def build_graph(saver: SqlCheckpointSaver, calls: dict[str, int]):  # type: ignore[no-untyped-def]
    def a(state: S) -> dict:
        calls["a"] += 1
        return {"log": ["a"]}

    def b(state: S) -> dict:
        decision = interrupt({"ask": "approve?"})
        return {"log": [f"b:{decision}"]}

    graph = StateGraph(S)
    graph.add_node("a", a)
    graph.add_node("b", b)
    graph.add_edge(START, "a")
    graph.add_edge("a", "b")
    graph.add_edge("b", END)
    return graph.compile(checkpointer=saver)


def test_checkpoints_survive_process_restart(platform: Platform) -> None:
    calls = {"a": 0}
    config = {"configurable": {"thread_id": "thread-1"}}
    first = build_graph(SqlCheckpointSaver(platform.db), calls)
    result = first.invoke({"log": []}, config)
    assert "__interrupt__" in result
    # a brand-new saver and engine (simulates a restarted worker) resumes from the database
    fresh_db = Database(platform.settings)
    resumed = build_graph(SqlCheckpointSaver(fresh_db), calls).invoke(Command(resume="yes"), config)
    assert resumed["log"] == ["a", "b:yes"]
    assert calls["a"] == 1  # completed nodes are not re-executed
    saver = SqlCheckpointSaver(fresh_db)
    assert len(list(saver.list(config))) >= 3
    saver.delete_thread("thread-1")
    assert saver.get_tuple(config) is None
    fresh_db.dispose()


def test_chunking_and_extraction() -> None:
    text = "\n\n".join(f"Paragraph {i} " + "word " * 120 for i in range(6))
    chunks = chunk_text(text, size=800, overlap=100)
    assert len(chunks) > 3 and all(len(c) <= 900 for c in chunks)
    html_text, ctype = extract_text("a.html", b"<html><script>alert(1)</script><p>Hello &amp; bye</p></html>")
    assert ctype == "text/html" and "alert" not in html_text and "Hello & bye" in html_text
    with pytest.raises(ValidationFailedError):
        extract_text("malware.exe", b"MZ")


def test_minimal_pdf_extraction() -> None:
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    import io

    buf = io.BytesIO()
    writer.write(buf)
    text, ctype = extract_text("blank.pdf", buf.getvalue())
    assert ctype == "application/pdf" and text == ""


def test_knowledge_ingestion_roots_search_and_isolation(platform: Platform, admin: Principal, tmp_path: Path) -> None:
    kn = platform.knowledge
    result = kn.ingest_directory(admin.workspace_id, "docs", EXAMPLES / "document-research/documents")
    assert result["files"] == 5
    again = kn.ingest_directory(admin.workspace_id, "docs", EXAMPLES / "document-research/documents")
    assert {r["status"] for r in again["results"]} == {"unchanged"}
    hits = kn.search(admin.workspace_id, ["docs"], "freshness SLA gold tier", top_k=3)
    assert hits and hits[0].source == "pipeline-sla-standard.md"
    other = add_user(platform, "bob", "admin", workspace_slug="team-b")
    assert kn.search(other.workspace_id, ["docs"], "freshness SLA gold tier") == []
    with pytest.raises(ValidationFailedError, match="approved knowledge roots"):
        kn.ingest_directory(admin.workspace_id, "docs", Path("/etc"))
    doc_id = hits[0].document_id
    with pytest.raises(NotFoundError):
        kn.read_document(other.workspace_id, ["docs"], doc_id)
    assert "2 hours" in kn.read_document(admin.workspace_id, ["docs"], doc_id)["text"]
    kn.delete_document(admin.workspace_id, doc_id)
    assert all(h.document_id != doc_id for h in kn.search(admin.workspace_id, ["docs"], "freshness SLA gold tier"))


def test_components_are_immutable_and_protected(platform: Platform, admin: Principal) -> None:
    comp = platform.components.create(admin.workspace_id, ComponentCreate(
        kind="system_instruction", name="be-brief", definition={"text": "Be brief."}))
    with pytest.raises(ConflictError, match="immutable"):
        platform.components.create(admin.workspace_id, ComponentCreate(
            kind="system_instruction", name="be-brief", definition={"text": "Changed!"}))
    with pytest.raises(ValidationFailedError):
        platform.components.create(admin.workspace_id, ComponentCreate(kind="validation_rule", name="bad", definition={}))
    assert comp["ref"] == "system_instruction/be-brief@1.0.0"
    assert any(c["ref"] == "system_instruction/be-brief@1.0.0" for c in platform.components.list(admin.workspace_id))


def test_published_component_cannot_be_deleted(platform: Platform, admin: Principal) -> None:
    from agent_factory.spec.models import ComponentRef
    from tests.conftest import create_built, example_spec

    comp = platform.components.create(admin.workspace_id, ComponentCreate(
        kind="system_instruction", name="team-guard", definition={"text": "Follow team conventions."}))
    spec = example_spec("data-pipeline-diagnostic/agent.yaml")
    spec.spec.components.append(ComponentRef(kind="system_instruction", name="team-guard", version="1.0.0"))
    agent_id = create_built(platform, admin, spec)
    run = platform.evaluations.start_run(admin, agent_id, wait=True)
    assert run["passed"], run["summary"]
    platform.agents.publish(admin, agent_id)
    with pytest.raises(InvalidStateError, match="pinned"):
        platform.components.delete(admin.workspace_id, comp["id"])
    assert platform.components.deprecate(admin.workspace_id, comp["id"])["status"] == "deprecated"
    used = next(c for c in platform.components.list(admin.workspace_id) if c["id"] == comp["id"])
    assert used["used_by_versions"] == 1


def test_templates_catalog_and_custom_import(platform: Platform, admin: Principal) -> None:
    templates = {t["name"]: t for t in platform.templates.list(admin.workspace_id)}
    assert len(templates) == 10
    for name, tpl in templates.items():
        spec = platform.templates.instantiate(admin.workspace_id, name, name=f"x-{name}"[:60])
        assert spec.metadata.template == f"{name}@{tpl['version']}"
        assert tpl["security_profile"]["summary"] and tpl["description"]
    import yaml

    doc = yaml.safe_load((platform.settings.templates_dir / "custom-agent.yaml").read_text())
    doc["metadata"]["name"] = "my-team-template"
    created = platform.templates.create(admin.workspace_id, doc)
    assert created["source"] == "custom"
    with pytest.raises(ConflictError):
        platform.templates.create(admin.workspace_id, doc)
    with pytest.raises(ValidationFailedError):
        platform.templates.create(admin.workspace_id, {"metadata": {"name": "Bad"}})
