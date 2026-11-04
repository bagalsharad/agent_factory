"""Operational intelligence computed from what executions recorded: metrics, health, SLOs, drift, costs, alerts.

Everything is derived on read from ``executions``, ``tool_invocations``, ``approvals`` and ``evaluation_runs``;
there is no separate metrics pipeline to run or keep consistent. Production traffic only: evaluation executions
are excluded (their quality is reported by the evaluation engine).
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select

from ..core.circuit import CircuitRegistry
from ..core.errors import BudgetExceededError, NotFoundError, ValidationFailedError
from ..core.ids import utcnow
from ..governance.audit import AuditLog
from ..governance.rbac import Principal
from ..model_gateway.gateway import ModelGateway
from ..storage.db import Database
from ..storage.models import (
    Agent,
    Approval,
    CostBudget,
    EvaluationRun,
    Execution,
    SloDefinition,
    ToolInvocation,
)

SLO_METRICS = {  # metric -> (direction, unit)
    "success_rate": (">=", "ratio"),
    "error_rate": ("<=", "ratio"),
    "p95_latency_ms": ("<=", "ms"),
    "evaluation_pass_rate": (">=", "ratio"),
}
DRIFT_MIN_SAMPLES = 5
DRIFT_THRESHOLD = 0.5  # relative change that counts as drift


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, max(0, round(q * len(ordered)) - 1))], 1)


def _ratio(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


class OperationsService:
    def __init__(self, db: Database, audit: AuditLog, gateway: ModelGateway, circuits: CircuitRegistry) -> None:
        self.db = db
        self.audit = audit
        self.gateway = gateway
        self.circuits = circuits

    # ================================================================== raw data
    def _executions(
        self,
        session: Any,
        workspace_id: str,
        since: datetime,
        until: datetime | None = None,
        agent_id: str | None = None,
    ) -> list[Execution]:
        query = select(Execution).where(
            Execution.workspace_id == workspace_id,
            Execution.mode != "evaluation",
            Execution.parent_execution_id.is_(None),
            Execution.created_at >= since,
        )
        if until is not None:
            query = query.where(Execution.created_at < until)
        if agent_id:
            query = query.where(Execution.agent_id == agent_id)
        return list(session.scalars(query))

    @staticmethod
    def _latency_ms(e: Execution) -> float | None:
        active = (e.budget_usage or {}).get("active_seconds")  # harness time, excludes waiting for approval
        return round(float(active) * 1000, 1) if active else None

    def _summarize(self, session: Any, executions: list[Execution]) -> dict[str, Any]:
        total = len(executions)
        by_status = Counter(e.status for e in executions)
        finished = [e for e in executions if e.status in {"completed", "failed", "budget_exceeded", "dead_lettered"}]
        timeouts = sum(
            1
            for e in executions
            if (e.error or {}).get("code") in {"timeout"}
            or ((e.error or {}).get("details") or {}).get("limit") == "timeout_seconds"
        )
        usage = [e.budget_usage or {} for e in executions]
        latencies = [x for x in (self._latency_ms(e) for e in finished) if x is not None]
        ids = [e.id for e in executions]
        tools: dict[str, Counter[str]] = {}
        approvals = Counter[str]()
        if ids:
            for inv in session.scalars(select(ToolInvocation).where(ToolInvocation.execution_id.in_(ids))):
                if inv.idempotency_key.startswith("approval:"):
                    continue
                tools.setdefault(inv.tool_name, Counter())[inv.status] += 1
            for approval in session.scalars(select(Approval).where(Approval.execution_id.in_(ids))):
                approvals[approval.status] += 1
        failed = by_status["failed"] + by_status["budget_exceeded"] + by_status["dead_lettered"]
        cost = round(sum(float(u.get("cost_usd", 0.0)) for u in usage), 6)
        decided = approvals["approved"] + approvals["rejected"]
        return {
            "executions": total,
            "by_status": dict(by_status),
            "success_rate": _ratio(by_status["completed"], len(finished)),
            "failure_rate": _ratio(failed, len(finished)),
            "timeout_rate": _ratio(timeouts, total),
            "retry_rate": _ratio(sum(1 for u in usage if u.get("retries", 0)), total),
            "retries": sum(int(u.get("retries", 0)) for u in usage),
            "p50_latency_ms": _pct(latencies, 0.5),
            "p95_latency_ms": _pct(latencies, 0.95),
            "input_tokens": sum(int(u.get("input_tokens", 0)) for u in usage),
            "output_tokens": sum(int(u.get("output_tokens", 0)) for u in usage),
            "model_calls": sum(int(u.get("model_calls", 0)) for u in usage),
            "cost_usd": cost,
            "cost_per_execution_usd": round(cost / total, 6) if total else None,
            "policy_violations": sum(c["denied"] for c in tools.values()),
            "approvals": {
                "requested": sum(approvals.values()),
                "approved": approvals["approved"],
                "rejected": approvals["rejected"],
                "approval_rate": _ratio(approvals["approved"], decided),
            },
            "tools": {
                name: {
                    "calls": sum(c.values()),
                    "success_rate": _ratio(c["completed"], c["completed"] + c["failed"]),
                    "failed": c["failed"],
                    "denied": c["denied"],
                    "in_doubt": c["in_doubt"],
                }
                for name, c in sorted(tools.items())
            },
        }

    def _agents(self, session: Any, workspace_id: str) -> dict[str, Agent]:
        return {a.id: a for a in session.scalars(select(Agent).where(Agent.workspace_id == workspace_id))}

    def _latest_release_run(self, session: Any, agent: Agent) -> EvaluationRun | None:
        if not agent.active_version_id:
            return None
        return session.scalars(
            select(EvaluationRun)
            .where(
                EvaluationRun.agent_version_id == agent.active_version_id,
                EvaluationRun.status == "completed",
                EvaluationRun.purpose == "release",
            )
            .order_by(EvaluationRun.created_at.desc())
            .limit(1)
        ).first()

    # ================================================================== metrics and health
    def metrics(self, principal: Principal, *, agent_id: str | None = None, hours: int = 24) -> dict[str, Any]:
        principal.require("executions:read")
        since = utcnow() - timedelta(hours=hours)
        with self.db.session() as session:
            agents = self._agents(session, principal.workspace_id)
            executions = self._executions(session, principal.workspace_id, since, agent_id=agent_id)
            per_agent: dict[str, list[Execution]] = {}
            for e in executions:
                per_agent.setdefault(e.agent_id, []).append(e)
            rows = []
            for aid, items in sorted(
                per_agent.items(), key=lambda kv: agents[kv[0]].name if kv[0] in agents else kv[0]
            ):
                run = self._latest_release_run(session, agents[aid]) if aid in agents else None
                rows.append(
                    {
                        "agent_id": aid,
                        "agent": agents[aid].name if aid in agents else aid,
                        **self._summarize(session, items),
                        "evaluation_pass_rate": (run.metrics or {}).get("pass_rate") if run else None,
                    }
                )
            workspace = self._summarize(session, executions)
        return {"window_hours": hours, "since": since.isoformat(), "workspace": workspace, "agents": rows}

    def timeseries(
        self, principal: Principal, *, agent_id: str | None = None, hours: int = 24, buckets: int = 24
    ) -> dict[str, Any]:
        """Executions per time bucket by outcome, with p95 latency and tokens, for activity charts."""
        principal.require("executions:read")
        buckets = max(1, min(buckets, 96))
        now = utcnow()
        since = now - timedelta(hours=hours)
        size = timedelta(hours=hours) / buckets
        rows: list[dict[str, Any]] = [
            {
                "start": (since + size * i).isoformat(),
                "completed": 0,
                "failed": 0,
                "other": 0,
                "tokens": 0,
                "_latencies": [],
            }
            for i in range(buckets)
        ]
        with self.db.session() as session:
            for e in self._executions(session, principal.workspace_id, since, agent_id=agent_id):
                index = min(buckets - 1, int((e.created_at - since) / size))
                row = rows[index]
                if e.status == "completed":
                    row["completed"] += 1
                elif e.status in {"failed", "budget_exceeded", "dead_lettered"}:
                    row["failed"] += 1
                else:
                    row["other"] += 1
                usage = e.budget_usage or {}
                row["tokens"] += int(usage.get("input_tokens", 0)) + int(usage.get("output_tokens", 0))
                if (latency := self._latency_ms(e)) is not None and e.status == "completed":
                    row["_latencies"].append(latency)
        for row in rows:
            row["p95_latency_ms"] = _pct(row.pop("_latencies"), 0.95)
        return {"window_hours": hours, "bucket_minutes": round(size.total_seconds() / 60, 1), "items": rows}

    def providers(self, principal: Principal) -> dict[str, Any]:
        principal.require("models:read")
        circuits = {c["name"]: c for c in self.circuits.snapshot()}
        return {
            "providers": [
                {**h.model_dump(), "circuit": circuits.get(f"model:{h.provider}", {"state": "closed"})}
                for h in self.gateway.health()
            ],
            "circuits": list(circuits.values()),
        }

    def agent_health(self, principal: Principal, agent_id: str, hours: int = 24) -> dict[str, Any]:
        principal.require("agents:read")
        with self.db.session() as session:
            agent = session.get(Agent, agent_id)
            if agent is None or agent.workspace_id != principal.workspace_id:
                raise NotFoundError("Agent not found")
            name = agent.name
        metrics = self.metrics(principal, agent_id=agent_id, hours=hours)
        row = metrics["agents"][0] if metrics["agents"] else {"executions": 0}
        slos = [s for s in self.slo_status(principal) if s["agent_id"] in (agent_id, None)]
        alerts = [a for a in self.alerts(principal) if a.get("agent_id") in (agent_id, None)]
        breached = [s for s in slos if s["compliant"] is False]
        status = "unknown" if not row.get("executions") else "degraded" if breached or alerts else "healthy"
        if any(a["severity"] == "critical" for a in alerts):
            status = "unhealthy"
        return {
            "agent_id": agent_id,
            "agent": name,
            "status": status,
            "window_hours": hours,
            "metrics": row,
            "slos": slos,
            "alerts": alerts,
            "drift": self.drift(principal, agent_id=agent_id, hours=hours),
        }

    # ================================================================== SLOs
    def create_slo(
        self,
        principal: Principal,
        *,
        name: str,
        metric: str,
        objective: float,
        agent_id: str | None = None,
        window_hours: int = 168,
    ) -> dict[str, Any]:
        principal.require("settings:write")
        if metric not in SLO_METRICS:
            raise ValidationFailedError(f"metric must be one of {', '.join(SLO_METRICS)}")
        if SLO_METRICS[metric][1] == "ratio" and not 0 < objective <= 1:
            raise ValidationFailedError("Ratio objectives are between 0 and 1 (e.g. 0.99)")
        with self.db.transaction() as session:
            if (
                agent_id
                and (a := session.get(Agent, agent_id)) is not None
                and a.workspace_id != principal.workspace_id
            ):
                raise NotFoundError("Agent not found")
            slo = SloDefinition(
                workspace_id=principal.workspace_id,
                agent_id=agent_id,
                name=name,
                metric=metric,
                objective=objective,
                window_hours=window_hours,
                created_by=principal.user_id,
            )
            session.add(slo)
            session.flush()
            slo_id = slo.id
        self.audit.record(
            "slo.create",
            "slo",
            slo_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"metric": metric, "objective": objective},
        )
        return next(s for s in self.slo_status(principal) if s["id"] == slo_id)

    def delete_slo(self, principal: Principal, slo_id: str) -> None:
        principal.require("settings:write")
        with self.db.transaction() as session:
            slo = session.get(SloDefinition, slo_id)
            if slo is None or slo.workspace_id != principal.workspace_id:
                raise NotFoundError("SLO not found")
            session.delete(slo)

    def slo_status(self, principal: Principal) -> list[dict[str, Any]]:
        principal.require("executions:read")
        out = []
        with self.db.session() as session:
            slos = list(
                session.scalars(select(SloDefinition).where(SloDefinition.workspace_id == principal.workspace_id))
            )
            agents = self._agents(session, principal.workspace_id)
            for slo in slos:
                since = utcnow() - timedelta(hours=slo.window_hours)
                executions = self._executions(session, principal.workspace_id, since, agent_id=slo.agent_id)
                finished = [
                    e for e in executions if e.status in {"completed", "failed", "budget_exceeded", "dead_lettered"}
                ]
                bad = sum(1 for e in finished if e.status != "completed")
                direction, unit = SLO_METRICS[slo.metric]
                value: float | None
                budget: float | None = None
                if slo.metric in {"success_rate", "error_rate"}:
                    value = (
                        _ratio(len(finished) - bad, len(finished))
                        if slo.metric == "success_rate"
                        else _ratio(bad, len(finished))
                    )
                    allowed = (1 - slo.objective) if slo.metric == "success_rate" else slo.objective
                    if finished:
                        allowed_bad = allowed * len(finished)
                        budget = round(1 - bad / allowed_bad, 3) if allowed_bad else (1.0 if bad == 0 else 0.0)
                elif slo.metric == "p95_latency_ms":
                    latencies = [x for x in (self._latency_ms(e) for e in finished) if x is not None]
                    value = _pct(latencies, 0.95)
                    if latencies:  # error budget: 5% of executions may be slower than the objective
                        slow = sum(1 for x in latencies if x > slo.objective)
                        budget = round(1 - slow / (0.05 * len(latencies)), 3)
                else:
                    agent = agents.get(slo.agent_id or "")
                    run = self._latest_release_run(session, agent) if agent else None
                    value = (run.metrics or {}).get("pass_rate") if run else None
                compliant = (
                    None if value is None else (value >= slo.objective if direction == ">=" else value <= slo.objective)
                )
                out.append(
                    {
                        "id": slo.id,
                        "name": slo.name,
                        "agent_id": slo.agent_id,
                        "agent": agents[slo.agent_id].name if slo.agent_id in agents else None,
                        "metric": slo.metric,
                        "objective": slo.objective,
                        "direction": direction,
                        "unit": unit,
                        "window_hours": slo.window_hours,
                        "value": value,
                        "compliant": compliant,
                        "samples": len(finished),
                        "error_budget_remaining": None if budget is None else max(-1.0, min(1.0, budget)),
                    }
                )
        return out

    # ================================================================== drift
    def drift(self, principal: Principal, *, agent_id: str | None = None, hours: int = 24) -> list[dict[str, Any]]:
        """Compare the latest window with the one before it; report metrics that moved by ≥50%."""
        principal.require("executions:read")
        now = utcnow()
        current_start, previous_start = now - timedelta(hours=hours), now - timedelta(hours=2 * hours)
        findings: list[dict[str, Any]] = []
        with self.db.session() as session:
            agents = self._agents(session, principal.workspace_id)
            targets = [agent_id] if agent_id else list(agents)
            for aid in targets:
                current = self._executions(session, principal.workspace_id, current_start, agent_id=aid)
                previous = self._executions(
                    session, principal.workspace_id, previous_start, current_start, agent_id=aid
                )
                if len(current) < DRIFT_MIN_SAMPLES or len(previous) < DRIFT_MIN_SAMPLES:
                    continue
                now_m, before_m = self._summarize(session, current), self._summarize(session, previous)

                def per_exec(m: dict[str, Any], key: str) -> float:
                    return (m[key] or 0) / m["executions"]

                pairs = {
                    "p95_latency_ms": (before_m["p95_latency_ms"], now_m["p95_latency_ms"]),
                    "tokens_per_execution": (
                        per_exec(before_m, "input_tokens") + per_exec(before_m, "output_tokens"),
                        per_exec(now_m, "input_tokens") + per_exec(now_m, "output_tokens"),
                    ),
                    "failure_rate": (before_m["failure_rate"], now_m["failure_rate"]),
                    "policy_violations_per_execution": (
                        per_exec(before_m, "policy_violations"),
                        per_exec(now_m, "policy_violations"),
                    ),
                }
                for metric, (before, after) in pairs.items():
                    if before is None or after is None:
                        continue
                    change = (after - before) / before if before else (1.0 if after else 0.0)
                    if abs(change) >= DRIFT_THRESHOLD and (before or after):
                        findings.append(
                            {
                                "agent_id": aid,
                                "agent": agents[aid].name if aid in agents else aid,
                                "metric": metric,
                                "previous": round(before, 4),
                                "current": round(after, 4),
                                "change": round(change, 3),
                            }
                        )
                tool_mix = self._tool_mix_shift(before_m["tools"], now_m["tools"])
                if tool_mix >= 0.3:
                    findings.append(
                        {
                            "agent_id": aid,
                            "agent": agents[aid].name if aid in agents else aid,
                            "metric": "tool_selection",
                            "previous": None,
                            "current": None,
                            "change": round(tool_mix, 3),
                        }
                    )
        return findings

    @staticmethod
    def _tool_mix_shift(before: dict[str, Any], after: dict[str, Any]) -> float:
        """Total-variation distance between the tool-call distributions of two windows (0 = same, 1 = disjoint)."""
        b_total = sum(t["calls"] for t in before.values()) or 1
        a_total = sum(t["calls"] for t in after.values()) or 1
        names = set(before) | set(after)
        return 0.5 * sum(
            abs(before.get(n, {}).get("calls", 0) / b_total - after.get(n, {}).get("calls", 0) / a_total) for n in names
        )

    # ================================================================== cost budgets
    @staticmethod
    def _period_start(period: str) -> datetime:
        now = utcnow()
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start.replace(day=1) if period == "month" else start

    def create_budget(
        self,
        principal: Principal,
        *,
        period: str = "month",
        max_cost_usd: float | None = None,
        max_tokens: int | None = None,
        agent_id: str | None = None,
        action: str = "block",
        warn_at: float = 0.8,
    ) -> dict[str, Any]:
        principal.require("settings:write")
        if period not in {"day", "month"} or action not in {"block", "warn"}:
            raise ValidationFailedError("period is day|month and action is block|warn")
        if max_cost_usd is None and max_tokens is None:
            raise ValidationFailedError("Set max_cost_usd, max_tokens or both")
        with self.db.transaction() as session:
            budget = CostBudget(
                workspace_id=principal.workspace_id,
                agent_id=agent_id,
                period=period,
                max_cost_usd=max_cost_usd,
                max_tokens=max_tokens,
                action=action,
                warn_at=warn_at,
            )
            session.add(budget)
            session.flush()
            budget_id = budget.id
        self.audit.record(
            "budget.create",
            "cost_budget",
            budget_id,
            actor_id=principal.user_id,
            workspace_id=principal.workspace_id,
            details={"period": period, "action": action},
        )
        return next(b for b in self.budget_status(principal) if b["id"] == budget_id)

    def delete_budget(self, principal: Principal, budget_id: str) -> None:
        principal.require("settings:write")
        with self.db.transaction() as session:
            budget = session.get(CostBudget, budget_id)
            if budget is None or budget.workspace_id != principal.workspace_id:
                raise NotFoundError("Budget not found")
            session.delete(budget)

    def _budget_rows(self, session: Any, workspace_id: str, agent_id: str | None = None) -> list[dict[str, Any]]:
        query = select(CostBudget).where(CostBudget.workspace_id == workspace_id)
        rows = []
        for b in session.scalars(query):
            if agent_id is not None and b.agent_id not in (None, agent_id):
                continue
            executions = self._executions(session, workspace_id, self._period_start(b.period), agent_id=b.agent_id)
            cost = round(sum(float((e.budget_usage or {}).get("cost_usd", 0.0)) for e in executions), 6)
            tokens = sum(
                int((e.budget_usage or {}).get("input_tokens", 0)) + int((e.budget_usage or {}).get("output_tokens", 0))
                for e in executions
            )
            used = max(
                cost / b.max_cost_usd if b.max_cost_usd else 0.0,
                tokens / b.max_tokens if b.max_tokens else 0.0,
            )
            rows.append(
                {
                    "id": b.id,
                    "agent_id": b.agent_id,
                    "period": b.period,
                    "action": b.action,
                    "warn_at": b.warn_at,
                    "max_cost_usd": b.max_cost_usd,
                    "max_tokens": b.max_tokens,
                    "spent_usd": cost,
                    "tokens": tokens,
                    "used": round(used, 4),
                    "state": "exhausted" if used >= 1 else "warning" if used >= b.warn_at else "ok",
                }
            )
        return rows

    def budget_status(self, principal: Principal) -> list[dict[str, Any]]:
        principal.require("executions:read")
        with self.db.session() as session:
            return self._budget_rows(session, principal.workspace_id)

    def enforce_budgets(self, workspace_id: str, agent_id: str) -> None:
        """Called before an execution starts. Blocking budgets refuse new executions once exhausted."""
        with self.db.session() as session:
            rows = self._budget_rows(session, workspace_id, agent_id)
        for row in rows:
            if row["state"] == "exhausted" and row["action"] == "block":
                scope = "agent" if row["agent_id"] else "workspace"
                limit = (
                    "max_cost_usd" if row["max_cost_usd"] and row["spent_usd"] >= row["max_cost_usd"] else "max_tokens"
                )
                used = row["spent_usd"] if limit == "max_cost_usd" else row["tokens"]
                raise BudgetExceededError(f"{scope}_{row['period']}_{limit}", used, row[limit])

    # ================================================================== alerts
    def alerts(self, principal: Principal) -> list[dict[str, Any]]:
        """Conditions that need an operator, computed now (not a notification history)."""
        principal.require("executions:read")
        out: list[dict[str, Any]] = []
        for slo in self.slo_status(principal):
            if slo["compliant"] is False:
                out.append(
                    {
                        "severity": "critical",
                        "kind": "slo_breached",
                        "agent_id": slo["agent_id"],
                        "message": f"SLO '{slo['name']}' breached: {slo['metric']} {slo['value']} "
                        f"(objective {slo['direction']} {slo['objective']})",
                    }
                )
            elif slo["error_budget_remaining"] is not None and slo["error_budget_remaining"] < 0.25:
                out.append(
                    {
                        "severity": "warning",
                        "kind": "error_budget_low",
                        "agent_id": slo["agent_id"],
                        "message": f"SLO '{slo['name']}' has {slo['error_budget_remaining']:.0%} error budget left",
                    }
                )
        for budget in self.budget_status(principal):
            if budget["state"] != "ok":
                out.append(
                    {
                        "severity": "critical" if budget["state"] == "exhausted" else "warning",
                        "kind": f"cost_budget_{budget['state']}",
                        "agent_id": budget["agent_id"],
                        "message": f"{budget['period']} budget {budget['used']:.0%} used",
                    }
                )
        for circuit in self.circuits.snapshot():
            if circuit["state"] != "closed":
                out.append(
                    {
                        "severity": "critical",
                        "kind": "circuit_open",
                        "agent_id": None,
                        "message": f"{circuit['name']} circuit is {circuit['state']}",
                    }
                )
        with self.db.session() as session:
            dead = session.scalars(
                select(Execution).where(
                    Execution.workspace_id == principal.workspace_id, Execution.status == "dead_lettered"
                )
            ).all()
            for e in dead:
                out.append(
                    {
                        "severity": "critical",
                        "kind": "dead_lettered",
                        "agent_id": e.agent_id,
                        "execution_id": e.id,
                        "message": "Execution dead-lettered after repeated worker loss",
                    }
                )
            doubtful = session.scalars(
                select(ToolInvocation).where(
                    ToolInvocation.workspace_id == principal.workspace_id, ToolInvocation.status == "in_doubt"
                )
            ).all()
            for inv in doubtful:
                out.append(
                    {
                        "severity": "critical",
                        "kind": "tool_in_doubt",
                        "agent_id": None,
                        "execution_id": inv.execution_id,
                        "message": f"'{inv.tool_name}' may or may not have run; an operator must confirm",
                    }
                )
        for finding in self.drift(principal):
            out.append(
                {
                    "severity": "warning",
                    "kind": "drift",
                    "agent_id": finding["agent_id"],
                    "message": f"{finding['agent']}: {finding['metric']} changed {finding['change']:+.0%}",
                }
            )
        return out
