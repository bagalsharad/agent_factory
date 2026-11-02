"""agent-factory CLI.

Commands operate on the local platform directly (same services as the API), so they work without a running
server. ``serve`` starts the API + dashboard; ``worker`` runs an external execution worker.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from .. import __version__
from ..core.config import get_settings
from ..core.errors import AgentFactoryError

app = typer.Typer(
    help="Agent Factory — Design, Build, Test, Govern, and Deploy Intelligent Agents.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
)
models_app = typer.Typer(help="Model providers and local models.", no_args_is_help=True)
agents_app = typer.Typer(help="Create, inspect, build and run agents.", no_args_is_help=True)
approvals_app = typer.Typer(help="Human approvals.", no_args_is_help=True)
examples_app = typer.Typer(help="Bundled example agents.", no_args_is_help=True)
db_app = typer.Typer(help="Database migrations, backup and restore.", no_args_is_help=True)
users_app = typer.Typer(help="Users and API keys (api_key / oidc auth modes).", no_args_is_help=True)
app.add_typer(models_app, name="models")
app.add_typer(agents_app, name="agents")
app.add_typer(approvals_app, name="approvals")
app.add_typer(examples_app, name="examples")
app.add_typer(db_app, name="db")
app.add_typer(users_app, name="users")

console = Console()
STATUS_STYLE = {
    "ok": "green",
    "warn": "yellow",
    "fail": "red",
    "info": "cyan",
    "completed": "green",
    "failed": "red",
    "waiting_approval": "yellow",
    "published": "green",
    "testing": "cyan",
}


def _platform(migrate: bool = True) -> Any:
    from ..core.logging import configure_logging
    from ..platform import create_platform

    settings = get_settings()
    configure_logging("WARNING", json_output=False)
    return create_platform(settings, migrate=migrate)


def _principal(platform: Any) -> Any:
    """CLI acts as the local administrator (local mode) or the user named by AF_CLI_SUBJECT."""
    import os

    from sqlalchemy import select

    from ..governance.identity import LOCAL_SUBJECT
    from ..governance.rbac import make_principal
    from ..storage.models import Membership, User

    subject = os.environ.get("AF_CLI_SUBJECT", LOCAL_SUBJECT)
    with platform.db.transaction() as session:
        user = session.scalars(select(User).where(User.subject == subject)).first()
        if user is None:
            if subject != LOCAL_SUBJECT:
                raise typer.BadParameter(f"Unknown user '{subject}'")
            user = platform.identity.ensure_user(session, LOCAL_SUBJECT, display_name="Local developer", role="admin")
        membership = session.scalars(select(Membership).where(Membership.user_id == user.id)).first()
        return make_principal(user.id, user.subject, user.display_name, membership.workspace_id, membership.role, "cli")


def _fail(exc: AgentFactoryError) -> None:
    console.print(f"[red]error[/red] [{exc.code}] {exc.message}")
    if exc.details:
        console.print_json(json.dumps(exc.details, default=str))
    raise typer.Exit(1)


def _out(data: Any, as_json: bool) -> bool:
    if as_json:
        console.print_json(json.dumps(data, default=str))
    return as_json


# ============================================================================ top-level commands
@app.command()
def version() -> None:
    """Print the version."""
    console.print(f"agent-factory {__version__}")


@app.command()
def doctor(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Preflight checks: OS, CPU, memory, disk, Python, uv, Docker, Ollama, models, port."""
    from ..doctor import run_checks

    result = run_checks(get_settings())
    if _out(result, json_output):
        return
    table = Table(title="Agent Factory doctor", show_lines=False)
    for col in ("check", "status", "detail", "hint"):
        table.add_column(col)
    for c in result["checks"]:
        table.add_row(c["name"], f"[{STATUS_STYLE.get(c['status'], 'white')}]{c['status']}[/]", c["detail"], c["hint"])
    console.print(table)
    console.print("\n[bold]Recommended local models for this machine[/bold] (never downloaded automatically):")
    for r in result["recommendations"]:
        installed = " [green](installed)[/green]" if r["model"] in result["installed_models"] else ""
        console.print(f"  • {r['model']:<14} {r['why']}{installed}\n      install: [cyan]{r['install']}[/cyan]")
    if not result["ok"]:
        raise typer.Exit(1)


@app.command()
def init(
    examples: Annotated[bool, typer.Option(help="Install the example agents")] = True,
    evaluate: Annotated[bool, typer.Option(help="Evaluate and publish passing examples")] = True,
) -> None:
    """Create the data directory, run migrations, seed templates/components and install examples."""
    settings = get_settings()
    platform = _platform()
    console.print(
        f"[green]✓[/green] database ready ({platform.db.dialect}) at {settings.resolved_database_url.split('@')[-1]}"
    )
    console.print(
        f"[green]✓[/green] {len(platform.templates.list(_principal(platform).workspace_id))} templates, "
        f"{len(platform.tools.definitions())} tools registered"
    )
    if examples:
        from ..examples import install_examples

        for entry in install_examples(platform, _principal(platform), evaluate=evaluate, publish=evaluate):
            if entry["kind"] == "knowledge":
                console.print(f"[green]✓[/green] knowledge '{entry['collection']}': {entry['files']} file(s) ingested")
            else:
                ev = entry.get("evaluation")
                ev_text = f" — evaluation {'passed' if ev['passed'] else 'FAILED'} ({ev['pass_rate']})" if ev else ""
                style = "green" if entry["status"] in {"published", "testing", "approved", "exists"} else "red"
                console.print(
                    f"[{style}]•[/{style}] {entry['name']}: {entry['status']}{ev_text}"
                    + (f" {entry.get('errors') or entry.get('error')}" if style == "red" else "")
                )
    console.print(f"\nNext: [cyan]agent-factory serve[/cyan] and open http://{settings.host}:{settings.port}")
    platform.stop()


@app.command()
def serve(
    host: Annotated[str | None, typer.Option()] = None,
    port: Annotated[int | None, typer.Option()] = None,
    reload: bool = False,
) -> None:
    """Start the API and dashboard (binds to loopback by default)."""
    import os

    import uvicorn

    if host:
        os.environ["AF_HOST"] = host
    if port:
        os.environ["AF_PORT"] = str(port)
    get_settings.cache_clear()
    settings = get_settings()
    problems = settings.startup_problems()
    if problems:
        for p in problems:
            console.print(f"[red]refusing to start:[/red] {p}")
        raise typer.Exit(2)
    console.print(
        f"Agent Factory {__version__} — profile={settings.profile.value} auth={settings.auth_mode.value} "
        f"→ http://{settings.host}:{settings.port}  (API docs: /docs)"
    )
    uvicorn.run(
        "agent_factory.api.asgi:app",
        host=settings.host,
        port=settings.port,
        reload=reload,
        log_config=None,
        proxy_headers=settings.profile.value == "production",
        server_header=False,
    )


@app.command()
def worker(concurrency: Annotated[int | None, typer.Option()] = None) -> None:
    """Run an external execution worker (for AF_WORKER_MODE=external deployments)."""
    import signal
    import threading

    from ..core.logging import configure_logging
    from ..observability.telemetry import configure_telemetry
    from ..platform import create_platform

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    configure_telemetry(settings)
    platform = create_platform(settings)
    if concurrency:
        platform.dispatcher.concurrency = concurrency
    platform.dispatcher.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    console.print(f"worker running (concurrency={platform.dispatcher.concurrency}); Ctrl+C to stop")
    stop.wait()
    console.print("draining worker…")
    platform.stop()


@app.command()
def status(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Platform status: database, migrations, providers, agents and executions."""
    platform = _platform()
    principal = _principal(platform)
    data = {
        "version": __version__,
        "profile": platform.settings.profile.value,
        "database": {"dialect": platform.db.dialect, **platform.migration_status()},
        "providers": [h.model_dump() for h in platform.gateway.health()],
        "agents": platform.agents.list(principal)["total"],
        "executions": platform.executions.stats(principal),
        "audit": platform.audit.verify(),
    }
    if not _out(data, json_output):
        console.print(f"[bold]Agent Factory {__version__}[/bold] ({data['profile']})")
        console.print(
            f"database: {data['database']['dialect']} — migrations "
            f"{'up to date' if data['database']['up_to_date'] else 'PENDING'}"
        )
        for p in data["providers"]:
            console.print(
                f"provider {p['provider']:<18} [{STATUS_STYLE.get('ok' if p['status'] == 'up' else 'warn')}]"
                f"{p['status']}[/] {p['detail']}"
            )
        console.print(f"agents: {data['agents']}   executions: {data['executions']}")
        console.print(
            f"audit chain: {'valid' if data['audit']['valid'] else 'BROKEN'} ({data['audit']['events_checked']} events)"
        )
    platform.stop()


@app.command()
def evaluate(
    agent: str,
    version: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run an agent's evaluation suite (synchronously) and print the report."""
    platform = _platform()
    principal = _principal(platform)
    try:
        agent_id = _resolve_agent(platform, principal, agent)
        run = platform.evaluations.start_run(principal, agent_id, version=version, wait=True)
    except AgentFactoryError as exc:
        _fail(exc)
    if not _out(run, json_output):
        table = Table(
            title=f"Evaluation {run['id'][:8]} — {run.get('agent_version')} — {'PASSED' if run['passed'] else 'FAILED'}"
        )
        for col in ("case", "kind", "result", "status", "failed checks"):
            table.add_column(col)
        for r in run["results"]:
            failed = ", ".join(c["name"] for c in r["checks"] if not c["passed"])
            result = (
                "pending review"
                if r["observed"].get("status") == "pending_review"
                else ("pass" if r["passed"] else "FAIL")
            )
            table.add_row(r.get("case_key", ""), r.get("kind", ""), result, str(r["observed"].get("status")), failed)
        console.print(table)
        console.print_json(json.dumps(run["metrics"]))
    platform.stop()
    if not run["passed"]:
        raise typer.Exit(1)


def _resolve_agent(platform: Any, principal: Any, ref: str) -> str:
    for item in platform.agents.list(principal, limit=200)["items"]:
        if ref in (item["id"], item["name"]):
            return str(item["id"])
    from ..core.errors import NotFoundError

    raise NotFoundError(f"Agent '{ref}' not found")


# ============================================================================ models
@models_app.command("list")
def models_list(provider: Annotated[str | None, typer.Option()] = None) -> None:
    """List providers and available models."""
    platform = _platform()
    for h in platform.gateway.health():
        console.print(f"[bold]{h.provider}[/bold] {h.status} — {h.detail}")
    models, errors = platform.gateway.list_models(provider)
    table = Table()
    for col in ("provider", "model", "tools", "structured", "context", "size"):
        table.add_column(col)
    for m in models:
        table.add_row(
            m.provider,
            m.name,
            "yes" if m.capabilities.tools else "no",
            "yes" if m.capabilities.structured_output else "no",
            str(m.context_window or "-"),
            f"{m.size_bytes / 1e9:.1f} GB" if m.size_bytes else "-",
        )
    console.print(table)
    for name, err in errors.items():
        console.print(f"[yellow]{name}[/yellow]: {err}")
    platform.stop()


@models_app.command("test")
def models_test(provider: str, model: str, prompt: str = "Reply with the single word: ready") -> None:
    """Send a test prompt to a provider/model."""
    from ..model_gateway.types import ChatMessage, ChatRequest

    platform = _platform()
    try:
        response = platform.gateway.chat(
            provider,
            ChatRequest(model=model, messages=[ChatMessage(role="user", content=prompt)], timeout_seconds=60),
            max_retries=0,
        )
    except AgentFactoryError as exc:
        _fail(exc)
    console.print(response.content)
    console.print(f"[dim]{response.latency_ms} ms, usage={response.usage.model_dump()}[/dim]")
    platform.stop()


# ============================================================================ agents
@agents_app.command("list")
def agents_list(json_output: Annotated[bool, typer.Option("--json")] = False) -> None:
    """List agents."""
    platform = _platform()
    data = platform.agents.list(_principal(platform), limit=200)
    if not _out(data, json_output):
        table = Table()
        for col in ("name", "latest", "status", "active", "workflow", "updated"):
            table.add_column(col)
        for a in data["items"]:
            latest = a["latest_version"] or {}
            active = a["active_version"] or {}
            table.add_row(
                a["name"],
                latest.get("version", "-"),
                f"[{STATUS_STYLE.get(latest.get('status', ''), 'white')}]{latest.get('status', '-')}[/]",
                active.get("version", "-"),
                a.get("workflow_type") or "-",
                a["updated_at"][:19],
            )
        console.print(table)
    platform.stop()


@agents_app.command("create")
def agents_create(
    file: Annotated[Path | None, typer.Option("--file", "-f", help="Spec YAML/JSON file")] = None,
    objective: Annotated[str | None, typer.Option(help="Natural-language requirement")] = None,
    name: Annotated[str | None, typer.Option()] = None,
    template: Annotated[str | None, typer.Option()] = None,
    build: Annotated[bool, typer.Option(help="Validate and build after saving")] = True,
) -> None:
    """Create an agent from a spec file, a template, or a natural-language objective."""
    from ..spec.loader import dump_yaml, load_spec

    platform = _platform()
    principal = _principal(platform)
    try:
        if file:
            spec = load_spec(file.read_text(encoding="utf-8"))
        elif objective:
            draft = platform.interpreter.interpret(principal.workspace_id, objective, name=name, template=template)
            console.print(f"[bold]Draft[/bold] from template {draft.template} (confidence {draft.confidence:.2f})")
            for line in draft.rationale + draft.assumptions:
                console.print(f"  • {line}")
            console.print(dump_yaml(draft.spec.to_document()))
            spec = draft.spec
        elif template and name:
            spec = platform.templates.instantiate(principal.workspace_id, template, name=name)
        else:
            raise typer.BadParameter("Use --file, --objective, or --template with --name")
        agent = platform.agents.create(principal, spec, created_via="cli")
        console.print(f"[green]created[/green] {agent['name']} ({agent['id']}) as draft")
        if build:
            report = platform.agents.build(principal, agent["id"])
            console.print(
                f"build: {'[green]success[/green]' if report['success'] else '[red]failed[/red]'} → {report['status']}"
            )
            for err in report["errors"]:
                console.print(f"  [red]✗[/red] {err.get('path', err.get('stage'))}: {err['message']}")
            for warn in report["warnings"]:
                console.print(f"  [yellow]![/yellow] {warn.get('message')}")
    except AgentFactoryError as exc:
        _fail(exc)
    platform.stop()


@agents_app.command("show")
def agents_show(agent: str) -> None:
    """Show an agent's latest specification and versions."""
    platform = _platform()
    principal = _principal(platform)
    try:
        data = platform.agents.get(principal, _resolve_agent(platform, principal, agent))
    except AgentFactoryError as exc:
        _fail(exc)
    console.print(data["spec_yaml"])
    for v in data["versions"]:
        console.print(f"  {v['version']:<10} {v['status']:<12} {v['created_at'][:19]}")
    platform.stop()


@agents_app.command("build")
def agents_build(agent: str, version: Annotated[str | None, typer.Option()] = None) -> None:
    """Validate and build an agent version."""
    platform = _platform()
    principal = _principal(platform)
    try:
        report = platform.agents.build(principal, _resolve_agent(platform, principal, agent), version)
    except AgentFactoryError as exc:
        _fail(exc)
    for stage in report["stages"]:
        console.print(f"  {stage['name']:<24} {stage['status']:<8} {stage['detail']}")
    console.print(f"status: {report['status']}")
    platform.stop()


@agents_app.command("publish")
def agents_publish(agent: str, version: Annotated[str | None, typer.Option()] = None) -> None:
    """Publish an approved version (requires a passing evaluation)."""
    platform = _platform()
    principal = _principal(platform)
    try:
        result = platform.agents.publish(principal, _resolve_agent(platform, principal, agent), version)
    except AgentFactoryError as exc:
        _fail(exc)
    console.print(f"[green]published[/green] {result['version']}")
    platform.stop()


@agents_app.command("run")
def agents_run(
    agent: str,
    input_json: Annotated[str, typer.Option("--input", "-i", help="JSON object or @file.json")],
    version: Annotated[str | None, typer.Option()] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Execute an agent synchronously and print the result and event timeline."""
    platform = _platform()
    principal = _principal(platform)
    raw = Path(input_json[1:]).read_text(encoding="utf-8") if input_json.startswith("@") else input_json
    try:
        data = json.loads(raw)
        execution = platform.executions.create(
            principal, _resolve_agent(platform, principal, agent), input_data=data, version=version, inline=True
        )
        result = platform.executions.run_now(execution["id"])
        events = platform.executions.events(principal, execution["id"])["items"]
    except AgentFactoryError as exc:
        _fail(exc)
    except json.JSONDecodeError as exc:
        raise typer.BadParameter(f"--input is not valid JSON: {exc}")
    if not _out({"execution": result, "events": events}, json_output):
        for e in events:
            console.print(
                f"[dim]{e['seq']:>3}[/dim] {e['type']:<26} {e.get('node_id') or '':<20} {e.get('status') or ''}"
            )
        style = STATUS_STYLE.get(result["status"], "white")
        console.print(f"\nexecution {result['id']} → [{style}]{result['status']}[/]")
        if result["status"] == "waiting_approval":
            console.print("Pending approval. Decide with: [cyan]agent-factory approvals list[/cyan]")
        if result.get("error"):
            console.print(f"[red]{result['error']}[/red]")
        if result.get("output") is not None:
            console.print_json(json.dumps(result["output"], default=str))
        console.print(f"[dim]usage: {result['budget_usage']}[/dim]")
    platform.stop()


# ============================================================================ approvals
@approvals_app.command("list")
def approvals_list(status: str = "pending") -> None:
    """List approvals."""
    platform = _platform()
    data = platform.approvals.list(_principal(platform), status=status)
    for a in data["items"]:
        console.print(
            f"{a['id']}  {a['status']:<9} {a['action']:<22} tool={a['tool_name']} exec={a['execution_id'][:8]} "
            f"risk={a['risk']}\n    rationale: {a['rationale'][:160]}"
        )
    if not data["items"]:
        console.print("no approvals")
    platform.stop()


def _decide(approval_id: str, decision: str, comment: str) -> None:
    platform = _platform()
    principal = _principal(platform)
    try:
        result = platform.approvals.decide(principal, approval_id, decision, comment)
        execution = platform.executions.run_now(result["execution_id"])
    except AgentFactoryError as exc:
        _fail(exc)
    console.print(f"approval {decision}; execution {execution['id']} → {execution['status']}")
    if execution.get("output") is not None:
        console.print_json(json.dumps(execution["output"], default=str))
    platform.stop()


@approvals_app.command("approve")
def approvals_approve(approval_id: str, comment: str = "") -> None:
    """Approve a pending action and resume its execution."""
    _decide(approval_id, "approved", comment)


@approvals_app.command("reject")
def approvals_reject(approval_id: str, comment: str = "") -> None:
    """Reject a pending action; the execution resumes without performing it."""
    _decide(approval_id, "rejected", comment)


# ============================================================================ examples
@examples_app.command("install")
def examples_install(evaluate: bool = True, publish: bool = True) -> None:
    """Install, build, evaluate and publish the bundled example agents."""
    from ..examples import install_examples

    platform = _platform()
    for entry in install_examples(platform, _principal(platform), evaluate=evaluate, publish=publish):
        console.print(entry)
    platform.stop()


# ============================================================================ database
@db_app.command("migrate")
def db_migrate() -> None:
    """Apply database migrations (Alembic upgrade head)."""
    platform = _platform()
    console.print(platform.migration_status())
    platform.stop()


@db_app.command("backup")
def db_backup(output: Annotated[Path | None, typer.Option("--output", "-o")] = None) -> None:
    """Back up the database (SQLite online backup; prints pg_dump guidance for PostgreSQL)."""
    import sqlite3

    settings = get_settings()
    if not settings.is_sqlite:
        console.print(
            'PostgreSQL: use `pg_dump --format=custom --file=agent_factory.dump "$AF_DATABASE_URL"` '
            "(see docs/deployment/backup-restore.md)."
        )
        raise typer.Exit(0)
    source = Path(settings.resolved_database_url.removeprefix("sqlite:///"))
    target = output or settings.data_dir / "backups" / f"agent_factory-{datetime.now():%Y%m%d-%H%M%S}.db"
    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    artifacts = settings.artifacts_dir
    if artifacts.exists():
        shutil.make_archive(str(target.with_suffix("")) + "-artifacts", "gztar", artifacts)
    console.print(f"[green]backup written[/green] {target}")


@db_app.command("restore")
def db_restore(backup: Path, yes: Annotated[bool, typer.Option("--yes")] = False) -> None:
    """Restore a SQLite backup (stop the server first). Overwrites the current database."""
    import sqlite3

    settings = get_settings()
    if not settings.is_sqlite:
        console.print('PostgreSQL: use `pg_restore --clean --dbname "$AF_DATABASE_URL" agent_factory.dump`.')
        raise typer.Exit(0)
    if not yes:
        typer.confirm(f"Overwrite {settings.resolved_database_url} with {backup}?", abort=True)
    target = Path(settings.resolved_database_url.removeprefix("sqlite:///"))
    with sqlite3.connect(backup) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    console.print(f"[green]restored[/green] {backup} → {target}. Run `agent-factory db migrate` if it is older.")


# ============================================================================ users / keys / governance
@users_app.command("create")
def users_create(
    subject: str,
    role: str = "viewer",
    email: Annotated[str | None, typer.Option()] = None,
    api_key: Annotated[bool, typer.Option(help="Also create an API key")] = True,
) -> None:
    """Create a user in the default workspace (optionally with an API key, shown once)."""
    from ..governance.rbac import Role

    if role not in {r.value for r in Role}:
        raise typer.BadParameter(f"role must be one of {[r.value for r in Role]}")
    platform = _platform()
    with platform.db.transaction() as session:
        user = platform.identity.ensure_user(session, subject, email=email, display_name=subject, role=role)
        user_id = user.id
    console.print(f"user {subject} ({user_id}) role={role}")
    if api_key:
        raw, _ = platform.identity.create_api_key(user_id, f"{subject}-cli")
        console.print(f"API key (store it now, shown once): [bold]{raw}[/bold]")
    platform.stop()


@app.command("audit-verify")
def audit_verify() -> None:
    """Verify the hash chain of the audit log."""
    platform = _platform()
    result = platform.audit.verify()
    console.print(result)
    platform.stop()
    if not result["valid"]:
        raise typer.Exit(1)


@app.command("purge")
def purge(older_than_days: Annotated[int, typer.Option()] = 30) -> None:
    """Delete terminal executions (events, checkpoints, tool records) older than N days."""
    platform = _platform()
    console.print(f"deleted {platform.executions.purge_older_than(older_than_days)} execution(s)")
    platform.stop()


def main() -> None:  # pragma: no cover
    try:
        app()
    except AgentFactoryError as exc:
        console.print(f"[red]{exc.code}[/red]: {exc.message}")
        sys.exit(1)


# ============================================================================ email connector
email_app = typer.Typer(help="Email connector setup (read-only IMAP + send-to-self).", no_args_is_help=True)
schedule_app = typer.Typer(help="Run agents on a schedule with macOS launchd.", no_args_is_help=True)
app.add_typer(email_app, name="email")
app.add_typer(schedule_app, name="schedule")


def _update_env_file(path: Path, values: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(values)
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in remaining.items()]
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    path.chmod(0o600)


@email_app.command("setup")
def email_setup(
    address: Annotated[str | None, typer.Option(help="Mailbox address, e.g. you@gmail.com")] = None,
    imap_host: Annotated[str, typer.Option()] = "imap.gmail.com",
    smtp_host: Annotated[str, typer.Option()] = "smtp.gmail.com",
    digest_dir: Annotated[Path | None, typer.Option(help="Where daily Markdown digests are saved")] = None,
) -> None:
    """Store the app password in the macOS Keychain and write non-secret email settings to ./.env."""
    from ..governance.secrets import KeyringSecretStore

    settings = get_settings()
    address = address or typer.prompt("Email address", default=settings.email_address or "")
    console.print(
        "Gmail: enable 2-Step Verification, then create an app password at "
        "https://myaccount.google.com/apppasswords (16 characters)."
    )
    password = typer.prompt("App password (input hidden)", hide_input=True).replace(" ", "")
    store = KeyringSecretStore()
    store.set(settings.email_password_secret, password)
    allow = sorted(set(settings.network_allowlist) | {imap_host, smtp_host})
    env_path = Path.cwd() / ".env"
    values = {
        "AF_EMAIL_ADDRESS": address,
        "AF_EMAIL_IMAP_HOST": imap_host,
        "AF_EMAIL_SMTP_HOST": smtp_host,
        "AF_SECRETS_BACKEND": "keyring",
        "AF_NETWORK_ALLOWLIST": ",".join(allow),
    }
    if digest_dir:
        values["AF_DIGEST_DIR"] = str(digest_dir.expanduser())
    _update_env_file(env_path, values)
    console.print(
        f"[green]✓[/green] app password stored in the macOS Keychain (service 'agent-factory', "
        f"item '{settings.email_password_secret}')"
    )
    console.print(f"[green]✓[/green] settings written to {env_path}")
    get_settings.cache_clear()
    email_test()


@email_app.command("test")
def email_test(hours: int = 24) -> None:
    """Log in read-only and count messages from the last N hours (nothing is modified)."""
    from datetime import UTC, datetime, timedelta

    from ..core.errors import ToolError
    from ..tools.builtin.email import ImapMailbox

    get_settings.cache_clear()
    settings = get_settings()
    try:
        messages, total = ImapMailbox(settings).fetch(
            mailbox="INBOX", since=datetime.now(UTC) - timedelta(hours=hours), max_messages=5, unread_only=False
        )
    except ToolError as exc:
        console.print(f"[red]✗[/red] {exc.message}")
        raise typer.Exit(1)
    console.print(
        f"[green]✓[/green] connected to {settings.email_imap_host} as {settings.email_address}: "
        f"{total} message(s) in the last {hours}h (read-only; nothing marked as read)"
    )
    for m in messages:
        console.print(f"   • {m['from'][:40]:<40} {m['subject'][:70]}")


# ============================================================================ schedules (launchd)
LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"
DAY_SETS = {"weekdays": [1, 2, 3, 4, 5], "daily": [0, 1, 2, 3, 4, 5, 6]}
DAY_NAMES = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}


def _label(agent: str) -> str:
    return f"com.agentfactory.{agent}"


@schedule_app.command("install")
def schedule_install(
    agent: str,
    at: Annotated[str, typer.Option("--at", help="Local time HH:MM")] = "07:00",
    days: Annotated[str, typer.Option(help="weekdays | daily | comma list like mon,wed,fri")] = "weekdays",
    input_json: Annotated[str, typer.Option("--input", "-i")] = "{}",
    print_only: Annotated[bool, typer.Option("--print", help="Only print the launchd plist")] = False,
) -> None:
    """Create a launchd job that runs `agent-factory agents run AGENT` at the given time (macOS)."""
    import plistlib
    import subprocess

    hour, minute = (int(x) for x in at.split(":"))
    weekdays = DAY_SETS.get(days) or [DAY_NAMES[d.strip()[:3].lower()] for d in days.split(",")]
    json.loads(input_json)  # validate early
    executable = Path(sys.executable).parent / "agent-factory"
    logs = Path.home() / "Library" / "Logs" / "AgentFactory"
    plist = {
        "Label": _label(agent),
        "ProgramArguments": [str(executable), "agents", "run", agent, "--input", input_json],
        "WorkingDirectory": str(Path.cwd()),
        "StartCalendarInterval": [{"Weekday": d, "Hour": hour, "Minute": minute} for d in weekdays],
        "StandardOutPath": str(logs / f"{agent}.log"),
        "StandardErrorPath": str(logs / f"{agent}.err.log"),
        "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "AF_LOG_JSON": "false", "COLUMNS": "120"},
        "ProcessType": "Background",
    }
    data = plistlib.dumps(plist)
    if print_only:
        console.print(data.decode())
        return
    if sys.platform != "darwin":
        console.print("[red]launchd schedules are macOS-only[/red]; use cron or a systemd timer with the same command.")
        raise typer.Exit(1)
    logs.mkdir(parents=True, exist_ok=True)
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    path = LAUNCH_AGENTS / f"{_label(agent)}.plist"
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(path)], capture_output=True, check=False)  # noqa: S603, S607
    path.write_bytes(data)
    proc = subprocess.run(["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True, check=False)  # noqa: S603, S607
    if proc.returncode != 0:
        console.print(f"[red]launchctl bootstrap failed:[/red] {proc.stderr.strip()}")
        raise typer.Exit(1)
    names = ",".join(k for k, v in DAY_NAMES.items() if v in weekdays)
    console.print(f"[green]✓[/green] {agent} scheduled at {at} on {names} ({path})")
    console.print(f"   logs: {logs / (agent + '.log')}   run now: launchctl kickstart {domain}/{_label(agent)}")
    console.print("   If the Mac is asleep at that time, launchd runs the job when it wakes.")


@schedule_app.command("list")
def schedule_list() -> None:
    """List Agent Factory launchd jobs."""
    import plistlib

    found = sorted(LAUNCH_AGENTS.glob("com.agentfactory.*.plist")) if LAUNCH_AGENTS.exists() else []
    if not found:
        console.print("no schedules installed")
    for path in found:
        plist = plistlib.loads(path.read_bytes())
        when = plist.get("StartCalendarInterval", [])
        days = ",".join(k for k, v in DAY_NAMES.items() if v in {w["Weekday"] for w in when})
        first = when[0] if when else {}
        console.print(
            f"{plist['Label']:<40} {first.get('Hour', 0):02d}:{first.get('Minute', 0):02d} {days}  "
            f"{' '.join(plist['ProgramArguments'][1:])}"
        )


@schedule_app.command("remove")
def schedule_remove(agent: str) -> None:
    """Remove an agent's launchd job."""
    import subprocess

    path = LAUNCH_AGENTS / f"{_label(agent)}.plist"
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}", str(path)], capture_output=True, check=False)  # noqa: S603, S607
    path.unlink(missing_ok=True)
    console.print(f"removed {path}")


# ============================================================================ media (local transcription)
media_app = typer.Typer(help="Local audio/video transcription (Whisper on Apple Silicon).", no_args_is_help=True)
app.add_typer(media_app, name="media")


@media_app.command("setup")
def media_setup(
    model: Annotated[str | None, typer.Option(help="Hugging Face repo of the MLX Whisper model")] = None,
) -> None:
    """Check ffmpeg and explicitly download the Whisper model (~1.6 GB for large-v3-turbo)."""
    import importlib.util

    settings = get_settings()
    repo = model or settings.whisper_model
    if not shutil.which("ffmpeg"):
        console.print("[red]✗[/red] ffmpeg not found — install it with: [cyan]brew install ffmpeg[/cyan]")
        raise typer.Exit(1)
    console.print("[green]✓[/green] ffmpeg found")
    if importlib.util.find_spec("mlx_whisper") is None:
        console.print(
            "[red]✗[/red] mlx-whisper not installed — run: [cyan]uv sync --extra media[/cyan] (Apple Silicon)"
        )
        raise typer.Exit(1)
    from huggingface_hub import snapshot_download

    console.print(f"Downloading {repo} (this is the only time the model is downloaded)…")
    path = snapshot_download(repo)
    console.print(f"[green]✓[/green] Whisper model ready at {path}")
    console.print(f"Approved media folders (AF_MEDIA_DIRS): {', '.join(settings.media_dirs)}")


@media_app.command("status")
def media_status() -> None:
    """Show whether local transcription is ready."""
    from ..tools.builtin.video import media_readiness

    problems = media_readiness(get_settings())
    for problem in problems:
        console.print(f"[yellow]![/yellow] {problem}")
    if not problems:
        console.print("[green]✓[/green] local transcription ready")
