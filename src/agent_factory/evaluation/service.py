"""Evaluation service: suites, runs, metrics, acceptance gates, human review and version comparison.

A run executes every case through the real runtime in ``evaluation`` mode (same harness, policies and
tools). Deterministic checks decide pass/fail. Human-review cases stay ``pending_review`` until an evaluator
records a verdict; they only block acceptance when listed in ``acceptance.required_cases``.
"""

from __future__ import annotations

import statistics
import threading
import time
from typing import Any

from sqlalchemy import select

from ..core.errors import NotFoundError, ValidationFailedError
from ..core.ids import content_hash, utcnow
from ..core.logging import get_logger
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..registry.agents import AgentService
from ..runtime.executor import ExecutionService
from ..spec.loader import load_spec
from ..spec.models import Acceptance, AgentSpec, EvalCaseSpec
from ..storage.db import Database
from ..storage.models import (
    Component,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    Execution,
    ExecutionEvent,
    ToolInvocation,
)
from .checks import groundedness, run_checks

log = get_logger(__name__)


def suite_dict(s: EvaluationSuite, cases: list[EvaluationCase] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": s.id,
        "name": s.name,
        "version": s.version,
        "agent_id": s.agent_id,
        "description": s.description,
        "acceptance": s.acceptance,
        "content_hash": s.content_hash,
        "created_at": s.created_at.isoformat(),
    }
    if cases is not None:
        out["cases"] = [
            {
                "id": c.id,
                "case_key": c.case_key,
                "name": c.name,
                "kind": c.kind,
                "input": c.input,
                "expected": c.expected,
                "config": c.config,
            }
            for c in cases
        ]
    return out


def run_dict(r: EvaluationRun, results: list[EvaluationResult] | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": r.id,
        "suite_id": r.suite_id,
        "agent_version_id": r.agent_version_id,
        "status": r.status,
        "passed": r.passed,
        "summary": r.summary,
        "metrics": r.metrics,
        "model_config": r.model_config_snapshot,
        "suite_hash": r.suite_hash,
        "spec_hash": r.spec_hash,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat(),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }
    if results is not None:
        out["results"] = [
            {
                "id": x.id,
                "case_id": x.case_id,
                "execution_id": x.execution_id,
                "passed": x.passed,
                "checks": x.checks,
                "observed": x.observed,
                "duration_ms": x.duration_ms,
            }
            for x in results
        ]
    return out


class EvaluationService:
    def __init__(self, db: Database, agents: AgentService, executions: ExecutionService, audit: AuditLog) -> None:
        self.db = db
        self.agents = agents
        self.executions = executions
        self.audit = audit

    # ================================================================== suites
    def _upsert_suite(
        self,
        workspace_id: str,
        agent_id: str | None,
        name: str,
        cases: list[EvalCaseSpec],
        acceptance: Acceptance,
        description: str = "",
    ) -> EvaluationSuite:
        payload = {
            "cases": [c.model_dump(mode="json") for c in cases],
            "acceptance": acceptance.model_dump(mode="json"),
        }
        digest = content_hash(payload)
        with self.db.transaction() as session:
            suites = session.scalars(
                select(EvaluationSuite).where(
                    EvaluationSuite.workspace_id == workspace_id, EvaluationSuite.name == name
                )
            ).all()
            for suite in suites:
                if suite.content_hash == digest:
                    session.expunge(suite)
                    return suite
            version = f"1.{len(suites)}.0"
            suite = EvaluationSuite(
                workspace_id=workspace_id,
                agent_id=agent_id,
                name=name,
                version=version,
                description=description,
                acceptance=acceptance.model_dump(mode="json"),
                content_hash=digest,
            )
            session.add(suite)
            session.flush()
            for case in cases:
                session.add(
                    EvaluationCase(
                        suite_id=suite.id,
                        case_key=case.id,
                        name=case.name,
                        kind=case.kind,
                        input=case.input,
                        expected=case.expected.model_dump(mode="json"),
                        config={
                            k: v
                            for k, v in case.model_dump(mode="json").items()
                            if k in {"model_override", "approval_decision", "limits_override", "fault_injection"} and v
                        },
                    )
                )
            session.flush()
            session.expunge(suite)
            return suite

    def create_suite(
        self,
        principal: Principal,
        *,
        name: str,
        cases: list[dict[str, Any]],
        acceptance: dict[str, Any] | None = None,
        agent_id: str | None = None,
        description: str = "",
    ) -> dict[str, Any]:
        principal.require("evaluations:write")
        if agent_id:
            self.agents.get(principal, agent_id)  # access check
        parsed = [EvalCaseSpec.model_validate(c) for c in cases]
        if not parsed:
            raise ValidationFailedError("A suite needs at least one case")
        suite = self._upsert_suite(
            principal.workspace_id, agent_id, name, parsed, Acceptance.model_validate(acceptance or {}), description
        )
        return self.get_suite(principal, suite.id)

    def list_suites(self, principal: Principal, agent_id: str | None = None) -> list[dict[str, Any]]:
        principal.require("evaluations:read")
        with self.db.session() as session:
            query = select(EvaluationSuite).where(EvaluationSuite.workspace_id == principal.workspace_id)
            if agent_id:
                query = query.where(EvaluationSuite.agent_id == agent_id)
            return [suite_dict(s) for s in session.scalars(query.order_by(EvaluationSuite.created_at.desc()))]

    def get_suite(self, principal: Principal, suite_id: str) -> dict[str, Any]:
        principal.require("evaluations:read")
        with self.db.session() as session:
            suite = session.get(EvaluationSuite, suite_id)
            if not suite or suite.workspace_id != principal.workspace_id:
                raise NotFoundError("Evaluation suite not found")
            cases = session.scalars(select(EvaluationCase).where(EvaluationCase.suite_id == suite_id)).all()
            return suite_dict(suite, list(cases))

    def _suite_for_version(self, principal: Principal, agent_id: str, spec: AgentSpec, suite_id: str | None) -> str:
        if suite_id:
            self.get_suite(principal, suite_id)
            return suite_id
        if spec.spec.evaluation.suite:
            with self.db.session() as session:
                named = session.scalars(
                    select(EvaluationSuite)
                    .where(
                        EvaluationSuite.workspace_id == principal.workspace_id,
                        EvaluationSuite.name == spec.spec.evaluation.suite,
                    )
                    .order_by(EvaluationSuite.created_at.desc())
                ).first()
            if named:
                return named.id
        cases = list(spec.spec.evaluation.cases)
        with self.db.session() as session:  # pinned evaluation datasets extend the inline cases
            for ref in spec.spec.components:
                if ref.kind == "evaluation_dataset":
                    comp = session.scalars(
                        select(Component).where(
                            Component.kind == ref.kind, Component.name == ref.name, Component.version == ref.version
                        )
                    ).first()
                    if comp:
                        cases += [EvalCaseSpec.model_validate(c) for c in comp.definition.get("cases", [])]
        if not cases:
            raise ValidationFailedError("This agent version has no evaluation cases or suite")
        suite = self._upsert_suite(
            principal.workspace_id,
            agent_id,
            f"{spec.metadata.name}-inline",
            cases,
            spec.spec.evaluation.acceptance,
            "Cases declared in the agent specification",
        )
        return suite.id

    # ================================================================== runs
    def start_run(
        self,
        principal: Principal,
        agent_id: str,
        *,
        version: str | None = None,
        suite_id: str | None = None,
        wait: bool = False,
    ) -> dict[str, Any]:
        principal.require("evaluations:run")
        _agent, version_record = self.agents.executable_version(principal, agent_id, version, prefer_latest=True)
        spec = load_spec(version_record.spec)
        sid = self._suite_for_version(principal, agent_id, spec, suite_id)
        with self.db.transaction() as session:
            suite = session.get(EvaluationSuite, sid)
            assert suite is not None
            run = EvaluationRun(
                workspace_id=principal.workspace_id,
                suite_id=sid,
                agent_version_id=version_record.id,
                status="running",
                model_config_snapshot=spec.spec.model.model_dump(),
                suite_hash=suite.content_hash,
                spec_hash=version_record.spec_hash,
                created_by=principal.user_id if principal.auth_method != "system" else None,
                started_at=utcnow(),
            )
            session.add(run)
            session.flush()
            run_id = run.id
        self.audit.record(
            "evaluation.start",
            "evaluation_run",
            run_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"version": version_record.version},
        )
        if wait:
            self._execute_run(principal, run_id, agent_id, version_record.id)
        else:
            threading.Thread(
                target=self._execute_run,
                args=(principal, run_id, agent_id, version_record.id),
                daemon=True,
                name=f"af-eval-{run_id[:8]}",
            ).start()
        return self.get_run(principal, run_id)

    def _observe(self, execution: dict[str, Any]) -> dict[str, Any]:
        with self.db.session() as session:
            invocations = session.scalars(
                select(ToolInvocation).where(ToolInvocation.execution_id == execution["id"])
            ).all()
            sources: list[str] = []
            for ev in session.scalars(
                select(ExecutionEvent).where(
                    ExecutionEvent.execution_id == execution["id"], ExecutionEvent.type == "retrieval"
                )
            ):
                sources += ev.payload.get("sources", [])
        called, denied, rejected, approvals = [], [], [], []
        for inv in invocations:
            if inv.idempotency_key.startswith("approval:"):
                approvals.append(inv.approval_id)
                continue
            if inv.status in {"completed", "failed"}:
                called.append(inv.tool_name)
            elif inv.status == "denied":
                denied.append(inv.tool_name)
            elif inv.status == "rejected":
                rejected.append(inv.tool_name)
            if isinstance(inv.result, dict) and inv.status == "completed":
                for item in (
                    (inv.result.get("output") or {}).get("results", [])
                    if isinstance(inv.result.get("output"), dict)
                    else []
                ):
                    if isinstance(item, dict) and item.get("source"):
                        sources.append(item["source"])
        return {
            "status": execution["status"],
            "output": execution.get("output"),
            "error": execution.get("error"),
            "duration_ms": execution.get("duration_ms") or 0,
            "usage": execution.get("budget_usage") or {},
            "tools_called": called,
            "tools_denied": denied,
            "tools_rejected": rejected,
            "approvals": approvals,
            "sources": sources,
        }

    def _execute_run(self, principal: Principal, run_id: str, agent_id: str, version_id: str) -> None:
        try:
            self._execute_run_inner(principal, run_id, agent_id, version_id)
        except Exception as exc:
            log.exception("evaluation run crashed")
            with self.db.transaction() as session:
                run = session.get(EvaluationRun, run_id)
                if run:
                    run.status, run.passed, run.finished_at = "error", False, utcnow()
                    run.summary = {"error": exc.__class__.__name__}

    def _execute_run_inner(self, principal: Principal, run_id: str, agent_id: str, version_id: str) -> None:
        with self.db.session() as session:
            run = session.get(EvaluationRun, run_id)
            assert run is not None
            suite = session.get(EvaluationSuite, run.suite_id)
            assert suite is not None
            cases = list(session.scalars(select(EvaluationCase).where(EvaluationCase.suite_id == suite.id)))
            from ..storage.models import AgentVersion

            version = session.get(AgentVersion, version_id)
            assert version is not None
            spec = load_spec(version.spec)
            acceptance = Acceptance.model_validate(suite.acceptance)
        results: list[dict[str, Any]] = []
        for case in cases:
            results.append(self._run_case(principal, run_id, agent_id, version.version, spec, case))
        summary, metrics, passed = self._summarize(results, acceptance)
        with self.db.transaction() as session:
            run = session.get(EvaluationRun, run_id)
            assert run is not None
            run.status, run.passed, run.summary, run.metrics, run.finished_at = (
                "completed",
                passed,
                summary,
                metrics,
                utcnow(),
            )
        new_status = self.agents.on_evaluation(version_id, passed)
        self.audit.record(
            "evaluation.complete",
            "evaluation_run",
            run_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            outcome="success" if passed else "failure",
            details={"pass_rate": summary.get("pass_rate"), "version_status": new_status},
        )

    def _run_case(
        self, principal: Principal, run_id: str, agent_id: str, version: str, spec: AgentSpec, case: EvaluationCase
    ) -> dict[str, Any]:
        from ..spec.models import EvalExpectation

        expected = EvalExpectation.model_validate(case.expected)
        config = case.config or {}
        started = time.perf_counter()
        execution_id: str | None = None
        if case.kind == "human_review":
            observed: dict[str, Any] = {"status": "pending_review"}
            checks: list[dict[str, Any]] = []
        else:
            options: dict[str, Any] = {}
            if config.get("model_override"):
                options["model_override"] = config["model_override"]
            if config.get("approval_decision") in {"approve", "reject"}:
                options["auto_approval"] = config["approval_decision"]
            if config.get("fault_injection"):
                options["fault_injection"] = config["fault_injection"]
            if config.get("limits_override"):
                options["limits_override"] = config["limits_override"]
            try:
                created = self.executions.create(
                    principal,
                    agent_id,
                    input_data=case.input,
                    version=version,
                    options=options,
                    mode="evaluation",
                    inline=True,
                    correlation_id=f"eval:{run_id}:{case.case_key}",
                )
                execution_id = created["id"]
                execution = self.executions.run_now(execution_id, worker_id=f"eval:{run_id[:8]}")
                observed = self._observe(execution)
            except ValidationFailedError as exc:
                observed = {
                    "status": "rejected_input",
                    "error": {"code": (exc.details or {}).get("code", exc.code), "message": exc.message},
                }
            checks = run_checks(expected, observed, spec.spec.outputs)
            score = groundedness(observed.get("output"), observed.get("sources", []))
            if score is not None:
                observed["groundedness"] = score
        passed = all(c["passed"] for c in checks) if checks else False
        duration = round((time.perf_counter() - started) * 1000, 2)
        with self.db.transaction() as session:
            session.add(
                EvaluationResult(
                    run_id=run_id,
                    case_id=case.id,
                    execution_id=execution_id,
                    passed=passed,
                    checks=checks,
                    observed={k: v for k, v in observed.items() if k != "output"} | {"output": observed.get("output")},
                    duration_ms=duration,
                )
            )
        return {
            "case_key": case.case_key,
            "kind": case.kind,
            "passed": passed,
            "observed": observed,
            "checks": checks,
            "duration_ms": duration,
        }

    def _summarize(
        self, results: list[dict[str, Any]], acceptance: Acceptance
    ) -> tuple[dict[str, Any], dict[str, Any], bool]:
        graded = [r for r in results if r["observed"].get("status") != "pending_review"]
        pending = [r["case_key"] for r in results if r["observed"].get("status") == "pending_review"]
        passed_cases = [r for r in graded if r["passed"]]
        pass_rate = len(passed_cases) / len(graded) if graded else 0.0
        latencies = [r["observed"].get("duration_ms") or 0 for r in graded if r["observed"].get("duration_ms")]
        usage = [r["observed"].get("usage", {}) for r in graded]
        completion = [
            r
            for r in graded
            if any(c["name"] == "status" for c in r["checks"])
            and next(c for c in r["checks"] if c["name"] == "status")["expected"] == "completed"
        ]
        schema_checks = [c for r in graded for c in r["checks"] if c["name"] == "output_schema_valid"]
        tool_checks = [
            c for r in graded for c in r["checks"] if c["name"].startswith(("tool_called", "tool_not_executed"))
        ]
        denial_checks = [c for r in graded for c in r["checks"] if c["name"].startswith("tool_denied")]
        grounded = [r["observed"]["groundedness"] for r in graded if r["observed"].get("groundedness") is not None]
        failed_status = [r for r in graded if r["observed"].get("status") in {"failed", "budget_exceeded"}]

        def ratio(items: list[dict[str, Any]]) -> float | None:
            return round(sum(1 for c in items if c["passed"]) / len(items), 3) if items else None

        metrics = {
            "pass_rate": round(pass_rate, 3),
            "task_completion_rate": round(
                sum(1 for r in completion if r["observed"]["status"] == "completed") / len(completion), 3
            )
            if completion
            else None,
            "output_schema_compliance": ratio(schema_checks),
            "correct_tool_selection": ratio(tool_checks),
            "unauthorized_action_rejection": ratio(denial_checks),
            "groundedness": round(statistics.fmean(grounded), 3) if grounded else None,
            "avg_latency_ms": round(statistics.fmean(latencies), 1) if latencies else None,
            "p95_latency_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1)
            if latencies
            else None,
            "model_calls": sum(u.get("model_calls", 0) for u in usage),
            "tool_calls": sum(u.get("tool_calls", 0) for u in usage),
            "input_tokens": sum(u.get("input_tokens", 0) for u in usage),
            "output_tokens": sum(u.get("output_tokens", 0) for u in usage),
            "retries": sum(u.get("retries", 0) for u in usage),
            "cost_usd": round(sum(u.get("cost_usd", 0.0) for u in usage), 6),
            "failure_rate": round(len(failed_status) / len(graded), 3) if graded else None,
        }
        required_ok = all(
            any(r["case_key"] == key and r["passed"] for r in results) for key in acceptance.required_cases
        )
        latency_ok = (
            acceptance.max_avg_latency_ms is None or (metrics["avg_latency_ms"] or 0) <= acceptance.max_avg_latency_ms
        )
        passed = bool(graded) and pass_rate >= acceptance.min_pass_rate and required_ok and latency_ok
        summary = {
            "total": len(results),
            "graded": len(graded),
            "passed": len(passed_cases),
            "failed": len(graded) - len(passed_cases),
            "pending_review": pending,
            "pass_rate": round(pass_rate, 3),
            "acceptance": acceptance.model_dump(mode="json"),
            "acceptance_checks": {
                "min_pass_rate": pass_rate >= acceptance.min_pass_rate,
                "required_cases": required_ok,
                "max_avg_latency_ms": latency_ok,
            },
            "failed_cases": [r["case_key"] for r in graded if not r["passed"]],
        }
        return summary, metrics, passed

    def review(
        self, principal: Principal, run_id: str, result_id: str, passed: bool, notes: str = ""
    ) -> dict[str, Any]:
        principal.require("evaluations:write")
        with self.db.transaction() as session:
            run = session.get(EvaluationRun, run_id)
            result = session.get(EvaluationResult, result_id)
            if not run or run.workspace_id != principal.workspace_id or not result or result.run_id != run_id:
                raise NotFoundError("Evaluation result not found")
            result.passed = passed
            result.checks = [
                *result.checks,
                {
                    "name": "human_review",
                    "passed": passed,
                    "expected": "reviewer verdict",
                    "observed": {"reviewer": principal.user_id, "notes": notes[:2000]},
                },
            ]
            result.observed = {**result.observed, "status": "reviewed"}
        return self.get_run(principal, run_id)

    def get_run(self, principal: Principal, run_id: str) -> dict[str, Any]:
        principal.require("evaluations:read")
        with self.db.session() as session:
            run = session.get(EvaluationRun, run_id)
            if not run or run.workspace_id != principal.workspace_id:
                raise NotFoundError("Evaluation run not found")
            results = list(session.scalars(select(EvaluationResult).where(EvaluationResult.run_id == run_id)))
            cases = {
                c.id: c for c in session.scalars(select(EvaluationCase).where(EvaluationCase.suite_id == run.suite_id))
            }
            data = run_dict(run, results)
            for item in data["results"]:
                case = cases.get(item["case_id"])
                if case:
                    item.update(
                        {
                            "case_key": case.case_key,
                            "case_name": case.name,
                            "kind": case.kind,
                            "input": case.input,
                            "expected": case.expected,
                        }
                    )
            from ..storage.models import AgentVersion

            version = session.get(AgentVersion, run.agent_version_id)
            suite = session.get(EvaluationSuite, run.suite_id)
            data["agent_id"] = version.agent_id if version else None
            data["agent_version"] = version.version if version else None
            data["suite"] = {"name": suite.name, "version": suite.version} if suite else None
            return data

    def list_runs(
        self, principal: Principal, *, agent_id: str | None = None, limit: int = 50, offset: int = 0
    ) -> dict[str, Any]:
        principal.require("evaluations:read")
        from ..storage.models import AgentVersion

        with self.db.session() as session:
            query = (
                select(EvaluationRun, AgentVersion.version, AgentVersion.agent_id)
                .join(AgentVersion, AgentVersion.id == EvaluationRun.agent_version_id)
                .where(EvaluationRun.workspace_id == principal.workspace_id)
            )
            if agent_id:
                query = query.where(AgentVersion.agent_id == agent_id)
            rows = session.execute(query.order_by(EvaluationRun.created_at.desc())).all()
            items = [{**run_dict(r), "agent_version": v, "agent_id": a} for r, v, a in rows]
        return {"items": items[offset : offset + limit], "total": len(items), "limit": limit, "offset": offset}

    def compare_versions(
        self, principal: Principal, agent_id: str, base: str, other: str, suite_id: str | None = None
    ) -> dict[str, Any]:
        """Run the same suite against two versions and diff per case."""
        _a, va = self.agents.executable_version(principal, agent_id, base)
        sid = suite_id or self._suite_for_version(principal, agent_id, load_spec(va.spec), None)
        run_a = self.start_run(principal, agent_id, version=base, suite_id=sid, wait=True)
        run_b = self.start_run(principal, agent_id, version=other, suite_id=sid, wait=True)
        by_case: dict[str, dict[str, Any]] = {}
        for label, run in (("base", run_a), ("other", run_b)):
            for item in run["results"]:
                by_case.setdefault(item.get("case_key", item["case_id"]), {})[label] = {
                    "passed": item["passed"],
                    "duration_ms": item["duration_ms"],
                    "status": item["observed"].get("status"),
                }
        regressions = [
            k for k, v in by_case.items() if v.get("base", {}).get("passed") and not v.get("other", {}).get("passed")
        ]
        improvements = [
            k for k, v in by_case.items() if not v.get("base", {}).get("passed") and v.get("other", {}).get("passed")
        ]
        return {
            "suite_id": sid,
            "base": {"version": base, "run_id": run_a["id"], "metrics": run_a["metrics"], "passed": run_a["passed"]},
            "other": {"version": other, "run_id": run_b["id"], "metrics": run_b["metrics"], "passed": run_b["passed"]},
            "cases": by_case,
            "regressions": regressions,
            "improvements": improvements,
        }

    def latest_status(self, principal: Principal) -> dict[str, Any]:
        with self.db.session() as session:
            runs = session.scalars(
                select(EvaluationRun)
                .where(EvaluationRun.workspace_id == principal.workspace_id)
                .order_by(EvaluationRun.created_at.desc())
                .limit(50)
            ).all()
            executions = session.scalars(
                select(Execution.id).where(
                    Execution.workspace_id == principal.workspace_id, Execution.mode == "evaluation"
                )
            ).all()
        return {
            "recent_runs": len(runs),
            "passed": sum(1 for r in runs if r.passed),
            "failed": sum(1 for r in runs if r.passed is False),
            "running": sum(1 for r in runs if r.status == "running"),
            "evaluation_executions": len(executions),
        }
