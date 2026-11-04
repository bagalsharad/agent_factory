"""Evaluation service: suites, runs, metrics, acceptance gates, human review and version comparison.

A run executes every case through the real runtime in ``evaluation`` mode (same harness, policies and
tools). Deterministic checks decide pass/fail. Human-review cases stay ``pending_review`` until an evaluator
records a verdict; they only block acceptance when listed in ``acceptance.required_cases``.
"""

from __future__ import annotations

import platform as host_platform
import statistics
import threading
import time
from collections.abc import Callable
from typing import Any

from sqlalchemy import select

from .. import __version__
from ..core.config import Settings
from ..core.errors import NotFoundError, PermissionDeniedError, ValidationFailedError
from ..core.ids import content_hash, utcnow
from ..core.logging import get_logger
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..model_gateway.gateway import ModelGateway
from ..registry.agents import AgentService
from ..runtime.executor import ExecutionService
from ..spec.loader import load_spec
from ..spec.models import Acceptance, AgentSpec, EvalCaseSpec, ModelSpec
from ..storage.db import Database
from ..storage.models import (
    Agent,
    AgentVersion,
    Component,
    EvaluationCase,
    EvaluationResult,
    EvaluationRun,
    EvaluationSuite,
    Execution,
    ExecutionEvent,
    ToolInvocation,
)
from ..tools.base import ToolDefinition
from .checks import groundedness, run_checks
from .gates import evaluate_gates, publish_blockers
from .judge import judge_output
from .security_suite import security_cases

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
        "provenance": r.provenance or {},
        "baseline_run_id": r.baseline_run_id,
        "purpose": r.purpose,
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
    def __init__(
        self,
        db: Database,
        agents: AgentService,
        executions: ExecutionService,
        audit: AuditLog,
        *,
        settings: Settings | None = None,
        tool_versions: Callable[[list[str]], dict[str, str]] | None = None,
        tool_catalog: Callable[[], dict[str, ToolDefinition]] | None = None,
        gateway: ModelGateway | None = None,
    ) -> None:
        self.db = db
        self.agents = agents
        self.executions = executions
        self.audit = audit
        self.settings = settings
        self.tool_versions = tool_versions or (lambda names: {})
        self.tool_catalog = tool_catalog or dict
        self.gateway = gateway

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
        if spec.spec.evaluation.security_suite:
            cases += security_cases(spec, self.tool_catalog())
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
        model_override: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run a suite. With ``model_override`` it is a comparison run: every case (except ones that pin their
        own model) uses that model, and the run never gates publishing or changes the version's status."""
        principal.require("evaluations:run")
        _agent, version_record = self.agents.executable_version(principal, agent_id, version, prefer_latest=True)
        spec = load_spec(version_record.spec)
        override = ModelSpec.model_validate(model_override) if model_override else None
        if override is not None and self.gateway is not None:
            self.gateway.provider(override.provider)  # raises if the provider is unknown or disabled
        sid = self._suite_for_version(principal, agent_id, spec, suite_id)
        with self.db.transaction() as session:
            suite = session.get(EvaluationSuite, sid)
            assert suite is not None
            baseline = None if override else self._baseline_run(session, agent_id, version_record.id)
            provenance = self._provenance(spec, suite, version_record.version)
            if override is not None:
                provenance["model"] = {
                    "provider": override.provider,
                    "model": override.model,
                    "profile": override.profile,
                }
                provenance["deterministic_mock_model"] = override.provider == "mock"
                provenance["model_override"] = True
            run = EvaluationRun(
                workspace_id=principal.workspace_id,
                suite_id=sid,
                agent_version_id=version_record.id,
                status="running",
                model_config_snapshot=(override or spec.spec.model).model_dump(),
                purpose="comparison" if override else "release",
                suite_hash=suite.content_hash,
                spec_hash=version_record.spec_hash,
                created_by=principal.user_id if principal.auth_method != "system" else None,
                started_at=utcnow(),
                provenance=provenance,
                baseline_run_id=baseline.id if baseline else None,
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
            version = session.get(AgentVersion, version_id)
            assert version is not None
            spec = load_spec(version.spec)
            acceptance = Acceptance.model_validate(suite.acceptance)
            baseline = self._baseline_context(session, run.baseline_run_id)
            purpose = run.purpose
            run_model = ModelSpec.model_validate(run.model_config_snapshot) if purpose == "comparison" else None
        results: list[dict[str, Any]] = []
        for case in cases:
            results.append(
                self._run_case(principal, run_id, agent_id, version.version, spec, case, acceptance, run_model)
            )
        summary, metrics, passed = self._summarize(results, acceptance, *baseline)
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
        new_status = self.agents.on_evaluation(version_id, passed) if purpose == "release" else "unchanged"
        self.audit.record(
            "evaluation.complete",
            "evaluation_run",
            run_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            outcome="success" if passed else "failure",
            details={"pass_rate": summary.get("pass_rate"), "version_status": new_status, "purpose": purpose},
        )

    def _run_case(
        self,
        principal: Principal,
        run_id: str,
        agent_id: str,
        version: str,
        spec: AgentSpec,
        case: EvaluationCase,
        acceptance: Acceptance | None = None,
        run_model: ModelSpec | None = None,
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
            elif run_model is not None:
                options["model_override"] = run_model.model_dump()
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
            judge = acceptance.judge if acceptance else None
            if (
                judge is not None
                and self.gateway is not None
                and observed.get("output") is not None
                and (not judge.cases or case.case_key in judge.cases)
            ):
                # Advisory: stored beside the deterministic checks, never changes this case's result.
                observed["judgement"] = judge_output(
                    self.gateway, judge, objective=spec.spec.objective, case_input=case.input, output=observed["output"]
                )
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
        self,
        results: list[dict[str, Any]],
        acceptance: Acceptance,
        baseline: list[dict[str, Any]] | None = None,
        baseline_info: dict[str, Any] | None = None,
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
        judgements = [r["observed"]["judgement"] for r in graded if r["observed"].get("judgement")]
        if judgements:
            scored = [j["overall"] for j in judgements if j.get("overall") is not None]
            metrics |= {
                "judge_score": round(statistics.fmean(scored), 3) if scored else None,
                "judged_cases": len(judgements),
                "judge_errors": len(judgements) - len(scored),
                "judge_cost_usd": round(sum(j.get("cost_usd", 0.0) for j in judgements), 6),
            }
        gates = evaluate_gates(results, acceptance, metrics, baseline=baseline, baseline_info=baseline_info)
        summary = {
            "total": len(results),
            "graded": len(graded),
            "passed": len(passed_cases),
            "failed": len(graded) - len(passed_cases),
            "pending_review": pending,
            "pass_rate": round(pass_rate, 3),
            "acceptance": acceptance.model_dump(mode="json"),
            "acceptance_checks": gates.checks,
            "gate_failures": gates.failures,
            "regression": gates.regression,
            "failed_cases": [r["case_key"] for r in graded if not r["passed"]],
        }
        return summary, metrics, gates.passed

    # ================================================================== provenance and baselines
    def _provenance(self, spec: AgentSpec, suite: EvaluationSuite, version: str) -> dict[str, Any]:
        """Everything needed to reproduce or explain the run: what was evaluated, with what, where."""
        body = spec.spec
        prompts = {
            "instructions": body.instructions,
            "steps": {s.id: s.prompt for s in body.workflow.steps if s.prompt},
        }
        return {
            "agent": {"name": spec.metadata.name, "version": version},
            "model": {"provider": body.model.provider, "model": body.model.model, "profile": body.model.profile},
            "deterministic_mock_model": body.model.provider == "mock",
            "prompt_hash": content_hash(prompts),
            "tools": self.tool_versions(body.tool_names),
            "components": [f"{c.kind}/{c.name}@{c.version}" for c in body.components],
            "suite": {"name": suite.name, "version": suite.version, "hash": suite.content_hash},
            "environment": {
                "platform_version": __version__,
                "python": host_platform.python_version(),
                "database": self.db.engine.dialect.name,
            },
        }

    @staticmethod
    def _baseline_run(session: Any, agent_id: str, version_id: str) -> EvaluationRun | None:
        """The approved baseline: the run that published the agent's active version (else its latest passing run)."""
        agent = session.get(Agent, agent_id)
        if not agent or not agent.active_version_id or agent.active_version_id == version_id:
            return None
        active = session.get(AgentVersion, agent.active_version_id)
        if active is None:
            return None
        published_by = (active.provenance or {}).get("evaluation_run_id")
        run = session.get(EvaluationRun, published_by) if published_by else None
        if run is not None and run.passed:
            return run
        return session.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.agent_version_id == active.id,
                EvaluationRun.status == "completed",
                EvaluationRun.passed.is_(True),
                EvaluationRun.purpose == "release",
            )
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        ).first()

    @staticmethod
    def _result_records(session: Any, run_id: str) -> list[dict[str, Any]]:
        rows = session.execute(
            select(EvaluationResult, EvaluationCase.case_key, EvaluationCase.kind)
            .join(EvaluationCase, EvaluationCase.id == EvaluationResult.case_id)
            .where(EvaluationResult.run_id == run_id)
        ).all()
        return [
            {
                "case_key": key,
                "kind": kind,
                "passed": result.passed,
                "observed": result.observed or {},
                "checks": result.checks or [],
                "duration_ms": result.duration_ms,
            }
            for result, key, kind in rows
        ]

    def _baseline_context(
        self, session: Any, baseline_run_id: str | None
    ) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
        if not baseline_run_id:
            return None, {"baseline_run_id": None, "baseline_version_id": None, "baseline_version": None}
        run = session.get(EvaluationRun, baseline_run_id)
        version = session.get(AgentVersion, run.agent_version_id) if run else None
        info = {
            "baseline_run_id": baseline_run_id,
            "baseline_version_id": version.id if version else None,
            "baseline_version": version.version if version else None,
        }
        return (self._result_records(session, baseline_run_id) if run else None), info

    def _recompute(self, run_id: str) -> tuple[bool, str]:
        """Re-apply the gates after a human verdict. Returns (passed, agent version id)."""
        with self.db.transaction() as session:
            run = session.get(EvaluationRun, run_id)
            assert run is not None
            suite = session.get(EvaluationSuite, run.suite_id)
            assert suite is not None
            results = self._result_records(session, run_id)
            baseline, info = self._baseline_context(session, run.baseline_run_id)
            summary, metrics, passed = self._summarize(
                results, Acceptance.model_validate(suite.acceptance), baseline, info
            )
            run.summary, run.metrics, run.passed = summary, metrics, passed
            return passed, run.agent_version_id

    def review(
        self, principal: Principal, run_id: str, result_id: str, passed: bool, notes: str = ""
    ) -> dict[str, Any]:
        principal.require("evaluations:write")
        with self.db.transaction() as session:
            run = session.get(EvaluationRun, run_id)
            result = session.get(EvaluationResult, result_id)
            if not run or run.workspace_id != principal.workspace_id or not result or result.run_id != run_id:
                raise NotFoundError("Evaluation result not found")
            case = session.get(EvaluationCase, result.case_id)
            if case is None or case.kind != "human_review":
                raise ValidationFailedError(
                    "Only human-review cases take a reviewer verdict; deterministic results cannot be overridden"
                )
            if run.status != "completed":
                raise ValidationFailedError("The evaluation run has not finished")
            version = session.get(AgentVersion, run.agent_version_id)
            self_review = version is not None and version.created_by == principal.user_id
            if self_review and self.settings is not None and not self.settings.allow_self_approval:
                raise PermissionDeniedError("Separation of duties: you cannot review your own agent version")
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
        run_passed, version_id = self._recompute(run_id)
        with self.db.session() as session:
            version = session.get(AgentVersion, version_id)
            latest = self.agents.latest_evaluation(session, version) if version else None
        # Only the latest run for the version's exact specification moves its lifecycle status.
        new_status = (
            self.agents.on_evaluation(version_id, run_passed) if latest and latest.id == run_id else "unchanged"
        )
        self.audit.record(
            "evaluation.review",
            "evaluation_run",
            run_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            outcome="success" if passed else "failure",
            details={"result_id": result_id, "run_passed": run_passed, "version_status": new_status},
        )
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

    def release_gate(self, principal: Principal, agent_id: str, version: str | None = None) -> dict[str, Any]:
        """Whether a version can be published now, and every reason it cannot (same rules as publish)."""
        principal.require("evaluations:read")
        agent, record = self.agents.load_version(principal, agent_id, version)
        with self.db.session() as session:
            v = session.get(AgentVersion, record.id)
            assert v is not None
            run = self.agents.latest_evaluation(session, v)
            blockers = publish_blockers(
                version_status=v.status,
                run={"passed": run.passed, "summary": run.summary} if run else None,
                active_version_id=agent.active_version_id,
                version_id=v.id,
            )
            if v.status != "published" and self.agents.dependencies is not None:
                blockers += self.agents.dependencies.blockers(v.id)
            run_view = (
                {
                    "id": run.id,
                    "passed": run.passed,
                    "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                    "checks": (run.summary or {}).get("acceptance_checks", {}),
                    "gate_failures": (run.summary or {}).get("gate_failures", []),
                    "regression": (run.summary or {}).get("regression"),
                    "pending_review": (run.summary or {}).get("pending_review", []),
                    "provenance": run.provenance or {},
                }
                if run
                else None
            )
            return {
                "agent_id": agent.id,
                "version": v.version,
                "status": v.status,
                "publishable": not blockers,
                "blockers": blockers,
                "evaluation": run_view,
            }

    def compare_models(
        self,
        principal: Principal,
        agent_id: str,
        models: list[dict[str, Any]],
        *,
        version: str | None = None,
        suite_id: str | None = None,
    ) -> dict[str, Any]:
        """Run one suite against several model configurations; comparison runs never gate publishing."""
        principal.require("evaluations:run")
        if not 1 <= len(models) <= 6:
            raise ValidationFailedError("Compare between one and six model configurations")
        _a, record = self.agents.executable_version(principal, agent_id, version, prefer_latest=True)
        sid = suite_id or self._suite_for_version(principal, agent_id, load_spec(record.spec), None)
        keys = (
            "pass_rate",
            "output_schema_compliance",
            "correct_tool_selection",
            "unauthorized_action_rejection",
            "task_completion_rate",
            "avg_latency_ms",
            "p95_latency_ms",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "failure_rate",
            "judge_score",
        )
        rows: list[dict[str, Any]] = []
        cases: dict[str, dict[str, bool]] = {}
        for model in models:
            run = self.start_run(
                principal, agent_id, version=record.version, suite_id=sid, wait=True, model_override=model
            )
            label = f"{run['model_config']['provider']}/{run['model_config']['model']}"
            checks = run["summary"].get("acceptance_checks", {})
            rows.append(
                {
                    "model": label,
                    "run_id": run["id"],
                    "gates_passed": run["passed"],
                    "security_gates_passed": checks.get("mandatory_kinds"),
                    "metrics": {k: run["metrics"].get(k) for k in keys},
                }
            )
            for item in run["results"]:
                cases.setdefault(item.get("case_key", item["case_id"]), {})[label] = item["passed"]
        return {"agent_id": agent_id, "version": record.version, "suite_id": sid, "models": rows, "cases": cases}

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
