"""Email connector: read-only IMAP, send-to-self only, injection-safe inputs, policy destinations."""

from __future__ import annotations

import imaplib
import smtplib
from datetime import datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from agent_factory.core.config import Settings
from agent_factory.governance.policy import Effect, PolicyEngine, ToolAuthorizationRequest
from agent_factory.governance.rbac import make_principal
from agent_factory.tools.base import ToolContext
from agent_factory.tools.builtin.email import auto_lookback_hours
from agent_factory.tools.registry import ToolExecutor, build_default_registry
from tests.conftest import example_spec

REGISTRY = build_default_registry()


def settings_for(tmp_path: Path, **kw: Any) -> Settings:
    return Settings(_env_file=None, data_dir=tmp_path, email_address="owner@example.com", digest_dir=tmp_path / "digests",
                    **kw)


def ctx(settings: Settings, key: str = "k1") -> ToolContext:
    return ToolContext(workspace_id="ws", execution_id="ex", agent_name="email-digest", settings=settings,
                       idempotency_key=key)


@pytest.fixture
def password(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setenv("AF_SECRET_EMAIL_APP_PASSWORD", "app-pass-123")
    return "app-pass-123"


def raw_message(subject: str, body: str, seen: bool) -> bytes:
    msg = EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "owner@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 30 Oct 2026 10:00:00 +0000"
    msg.set_content(body)
    return bytes(msg)


class FakeIMAP:
    instances: list[FakeIMAP] = []

    def __init__(self, host: str, port: int, ssl_context: Any = None, timeout: float | None = None) -> None:
        self.host, self.port, self.commands = host, port, []
        now = datetime.now().astimezone()
        recent = imaplib.Time2Internaldate(now - timedelta(hours=2)).encode()
        old = imaplib.Time2Internaldate(now - timedelta(days=5)).encode()
        self.store = {
            b"11": (recent, b"(\\Seen)", raw_message("Read me", "Already seen body", True)),
            b"12": (recent, b"()", raw_message("Deadline Friday", "Please approve the budget by Friday.", False)),
            b"10": (old, b"(\\Seen)", raw_message("Old", "Too old", True)),
        }
        FakeIMAP.instances.append(self)

    def login(self, user: str, password: str) -> tuple[str, list[bytes]]:
        self.commands.append(("login", user, password))
        if password != "app-pass-123":
            raise imaplib.IMAP4.error("auth failed")
        return "OK", [b""]

    def select(self, mailbox: str, readonly: bool = False) -> tuple[str, list[bytes]]:
        self.commands.append(("select", mailbox, readonly))
        return "OK", [b"3"]

    def uid(self, command: str, *args: Any) -> tuple[str, list[Any]]:
        self.commands.append(("uid", command, *args))
        if command == "search":
            return "OK", [b" ".join(self.store)]
        uids = args[0].split(",")
        if "RFC822.SIZE" in args[1]:
            return "OK", [f"1 (UID {u} RFC822.SIZE 900 INTERNALDATE ".encode() + self.store[u.encode()][0]
                          + b" FLAGS " + self.store[u.encode()][1] + b")" for u in uids]
        assert "BODY.PEEK" in args[1], "must never fetch with BODY[] (would mark messages as read)"
        u = uids[0].encode()
        return "OK", [(b"1 (UID " + u + b" BODY[] {900}", self.store[u][2]), b")"]

    def logout(self) -> None:
        self.commands.append(("logout",))


def test_imap_fetch_is_read_only_and_windowed(tmp_path: Path, password: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    result = ToolExecutor().execute(REGISTRY.get("fetch_recent_emails"), {"since_hours": 24}, ctx(settings_for(tmp_path)))
    assert result.ok, result.error
    out = result.output
    assert out["total"] == 2 and out["returned"] == 2 and out["unread"] == 1  # 5-day-old message excluded
    assert {m["subject"] for m in out["messages"]} == {"Read me", "Deadline Friday"}
    conn = FakeIMAP.instances[-1]
    assert ("select", '"INBOX"', True) in conn.commands  # read-only selection
    assert conn.host == "imap.gmail.com" and conn.commands[-1] == ("logout",)


def test_imap_bad_password_is_a_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    monkeypatch.setenv("AF_SECRET_EMAIL_APP_PASSWORD", "wrong")
    result = ToolExecutor().execute(REGISTRY.get("fetch_recent_emails"), {}, ctx(settings_for(tmp_path)))
    assert not result.ok and "app password" in result.error["message"]
    monkeypatch.delenv("AF_SECRET_EMAIL_APP_PASSWORD")
    missing = ToolExecutor().execute(REGISTRY.get("fetch_recent_emails"), {}, ctx(settings_for(tmp_path)))
    assert "email setup" in missing.error["message"]


def test_fixture_mailbox_parses_html_and_flags(tmp_path: Path) -> None:
    result = ToolExecutor().execute(REGISTRY.get("fetch_recent_emails"), {"source": "fixture"}, ctx(settings_for(tmp_path)))
    out = result.output
    assert out["returned"] == 7 and out["source"] == "fixture"
    invoice = next(m for m in out["messages"] if m["subject"].startswith("Invoice"))
    assert "<b>" not in invoice["snippet"] and "$240.00" in invoice["snippet"]
    unread = ToolExecutor().execute(REGISTRY.get("fetch_recent_emails"), {"source": "fixture", "unread_only": True},
                                    ctx(settings_for(tmp_path)))
    assert all(m["unread"] for m in unread.output["messages"]) and unread.output["returned"] == 4


class FakeSMTP:
    sent: list[EmailMessage] = []

    def __init__(self, host: str, port: int, context: Any = None, timeout: float | None = None) -> None:
        self.host = host

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None

    def login(self, user: str, password: str) -> None:
        assert password == "app-pass-123"

    def send_message(self, msg: EmailMessage, from_addr: str, to_addrs: list[str]) -> None:
        assert to_addrs == ["owner@example.com"]
        FakeSMTP.sent.append(msg)


def test_send_goes_only_to_owner_and_never_twice(tmp_path: Path, password: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTP)
    tool = REGISTRY.get("send_digest_to_self")
    assert "to" not in tool.definition.input_schema["properties"]  # no recipient parameter exists
    args = {"subject": "Email digest", "markdown": "# Digest\n- item"}
    first = ToolExecutor().execute(tool, args, ctx(settings_for(tmp_path), key="exec:email_me:email_me"))
    again = ToolExecutor().execute(tool, args, ctx(settings_for(tmp_path), key="exec:email_me:email_me"))
    assert first.ok and first.output["sent_to"] == "owner@example.com"
    assert again.output["duplicate"] is True and len(FakeSMTP.sent) == 1
    sneaky = ToolExecutor().execute(tool, {**args, "to": "collector@attacker.example"}, ctx(settings_for(tmp_path), key="k2"))
    assert sneaky.ok and FakeSMTP.sent[-1]["To"] == "owner@example.com"  # unknown fields are ignored
    injected = ToolExecutor().execute(tool, {"subject": "Hi\r\nBcc: collector@attacker.example", "markdown": "x"},
                                      ctx(settings_for(tmp_path), key="k3"))
    assert not injected.ok and injected.error["code"] == "invalid_arguments"  # header injection blocked


def test_save_digest_uses_fixed_dated_filename(tmp_path: Path) -> None:
    settings = settings_for(tmp_path)
    result = ToolExecutor().execute(REGISTRY.get("save_digest_file"), {"markdown": "# Digest"}, ctx(settings))
    path = Path(result.output["path"])
    assert path.parent == settings.digest_dir and path.name.startswith("email-digest-") and path.suffix == ".md"
    dry = ToolExecutor().execute(REGISTRY.get("save_digest_file"), {"markdown": "# Digest", "dry_run": True}, ctx(settings))
    assert Path(dry.output["path"]).is_relative_to(settings.sandbox_dir)  # type: ignore[arg-type]


def test_notify_dry_run_and_argv_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class Done:
        returncode, stderr = 0, ""

    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: calls.append(cmd) or Done())
    monkeypatch.setattr("sys.platform", "darwin")
    tool = REGISTRY.get("notify_desktop")
    assert ToolExecutor().execute(tool, {"title": "t", "message": "m", "dry_run": True}, ctx(settings_for(tmp_path))).output == {
        "delivered": False, "reason": "dry run"}
    evil = 'x" & do shell script "rm -rf ~" & "'
    assert ToolExecutor().execute(tool, {"title": "Digest", "message": evil}, ctx(settings_for(tmp_path))).ok
    assert calls[-1][-1] == evil and all(evil not in part for part in calls[-1][:-2])  # data passed as argv only


def test_policy_resolves_configured_destinations(tmp_path: Path) -> None:
    body = example_spec("email-digest/agent.yaml").spec
    principal = make_principal("u", "u", "u", "ws", "developer", "test")
    tool = REGISTRY.get("fetch_recent_emails")

    def decide(settings: Settings, args: dict[str, Any]) -> Any:
        return PolicyEngine(settings).evaluate_tool(ToolAuthorizationRequest(
            principal=principal, workspace_id="ws", agent=body, tool_name=tool.name, tool=tool.definition,
            tool_enabled=True, arguments=args, destination_host=tool.network_destination(args, settings),
            destination_resolved=True))

    closed = settings_for(tmp_path)
    assert decide(closed, {}).rules == ["network.allowlist"]  # imap.gmail.com not in AF_NETWORK_ALLOWLIST
    assert decide(closed, {"source": "fixture"}).effect == Effect.ALLOW  # no network for the sample mailbox
    open_ = settings_for(tmp_path, network_allowlist=["imap.gmail.com", "smtp.gmail.com"])
    assert decide(open_, {}).effect == Effect.ALLOW
    redirected = settings_for(tmp_path, network_allowlist=["imap.gmail.com"], email_imap_host="imap.evil.example")
    assert decide(redirected, {}).rules == ["network.allowlist"]


def test_monday_covers_the_weekend() -> None:
    assert auto_lookback_hours(datetime(2026, 11, 2, 7, 0)) == 72  # Monday
    assert auto_lookback_hours(datetime(2026, 11, 3, 7, 0)) == 24
