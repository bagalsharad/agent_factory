"""Email connector: read-only IMAP fetch, local digest file, send-to-self SMTP, and a desktop notification.

Safety properties:

* **Read-only mailbox access.** The mailbox is selected with ``readonly=True`` and bodies are fetched with
  ``BODY.PEEK``, so messages are never marked as read, moved or deleted.
* **Send-to-self only.** ``send_digest_to_self`` has no recipient parameter: it can only send to the configured
  owner address (``AF_EMAIL_ADDRESS``). Header injection is blocked by input validation.
* **Credentials never reach the model.** The app password is read from the secret store at call time.
* **Network destinations come from configuration**, not from arguments, and must be in both the platform and the
  agent allowlists (policy engine).
* Email content is untrusted. Use these tools from deterministic workflow steps so that text inside an email can
  never select or parameterize a tool.

``source: fixture`` reads the bundled sample mailbox (tests, demos, evaluations) and ``dry_run: true`` writes
delivery side effects to the local sandbox instead of sending.
"""

from __future__ import annotations

import contextlib
import email
import email.policy
import email.utils
import hashlib
import html
import imaplib
import json
import re
import smtplib
import ssl
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from ...core.config import Settings
from ...core.errors import AgentFactoryError, ToolError
from ...spec.models import RiskLevel
from ..base import AuditConfig, SideEffect, Tool, ToolContext

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SNIPPET_CHARS = 600
MAX_SCAN = 400
MAX_BODY_BYTES = 2_000_000
_lock = threading.Lock()


def auto_lookback_hours(now: datetime | None = None) -> int:
    """24 hours, except Monday which covers the weekend (72 hours)."""
    now = now or datetime.now().astimezone()
    return 72 if now.weekday() == 0 else 24


def _credentials(settings: Settings) -> tuple[str, str]:
    from ...governance.secrets import build_secret_store

    if not settings.email_address:
        raise ToolError("Email is not configured: run `agent-factory email setup` (AF_EMAIL_ADDRESS is empty)")
    try:
        password = build_secret_store(settings).get(settings.email_password_secret)
    except AgentFactoryError as exc:
        raise ToolError(
            f"Email app password '{settings.email_password_secret}' is not in the secret store: "
            "run `agent-factory email setup`"
        ) from exc
    return settings.email_address, password


def _plain_text(msg: email.message.Message) -> str:
    """Prefer text/plain; fall back to tag-stripped text/html. Attachments are ignored."""
    if not isinstance(msg, EmailMessage):
        return ""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, ValueError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else str(payload)
    if part.get_content_subtype() == "html":
        content = re.sub(r"(?is)<(script|style).*?</\1>", " ", content)
        content = html.unescape(re.sub(r"<[^>]+>", " ", content))
    return re.sub(r"\s+", " ", content).strip()


def _has_attachments(msg: email.message.Message) -> bool:
    return any(part.get_content_disposition() == "attachment" for part in msg.walk())


def _summarize_message(raw: bytes, *, uid: str, unread: bool, received: datetime | None) -> dict[str, Any]:
    msg = email.message_from_bytes(raw, policy=email.policy.default)
    text = _plain_text(msg)
    date = received
    if date is None and msg.get("date"):
        try:
            date = email.utils.parsedate_to_datetime(str(msg["date"]))
        except (TypeError, ValueError):
            date = None
    return {
        "uid": uid,
        "from": str(msg.get("from", ""))[:200],
        "to": str(msg.get("to", ""))[:300],
        "subject": str(msg.get("subject", "(no subject)"))[:300],
        "date": date.astimezone(UTC).isoformat() if date else None,
        "unread": unread,
        "has_attachments": _has_attachments(msg),
        "snippet": text[:SNIPPET_CHARS],
    }


def _imap_date(dt: datetime) -> str:
    return f"{dt.day:02d}-{MONTHS[dt.month - 1]}-{dt.year}"


def _meta_of(item: Any) -> bytes | None:
    if isinstance(item, tuple):
        return item[0] if isinstance(item[0], bytes) else None
    return item if isinstance(item, bytes) else None


class ImapMailbox:
    """Minimal read-only IMAP client (also used by `agent-factory email test`)."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def fetch(
        self, *, mailbox: str, since: datetime, max_messages: int, unread_only: bool
    ) -> tuple[list[dict[str, Any]], int]:
        address, password = _credentials(self.settings)
        context = ssl.create_default_context()
        try:
            conn = imaplib.IMAP4_SSL(
                self.settings.email_imap_host, self.settings.email_imap_port, ssl_context=context, timeout=30
            )
        except (OSError, imaplib.IMAP4.error) as exc:
            raise ToolError(
                f"Cannot connect to {self.settings.email_imap_host}: {exc.__class__.__name__}", retryable=True
            ) from exc
        try:
            try:
                conn.login(address, password)
            except imaplib.IMAP4.error as exc:
                raise ToolError(
                    "IMAP login failed: check the address and the app password "
                    "(Gmail needs 2-Step Verification and an app password)"
                ) from exc
            status, _ = conn.select(f'"{mailbox}"', readonly=True)
            if status != "OK":
                raise ToolError(f"Mailbox '{mailbox}' not found")
            criteria = ["SINCE", _imap_date(since - timedelta(days=1))] + (["UNSEEN"] if unread_only else [])
            status, data = conn.uid("search", *criteria)
            uids = (data[0] or b"").split()[-MAX_SCAN:] if status == "OK" and data else []
            if not uids:
                return [], 0
            # pass 1: dates, flags and sizes only
            status, data = conn.uid("fetch", b",".join(uids).decode(), "(UID INTERNALDATE FLAGS RFC822.SIZE)")
            candidates: list[tuple[datetime, str, bool, int]] = []
            for item in data or []:
                meta = _meta_of(item)
                if not meta or b"UID" not in meta:
                    continue
                uid_match = re.search(rb"UID (\d+)", meta)
                size_match = re.search(rb"RFC822\.SIZE (\d+)", meta)
                tt = imaplib.Internaldate2tuple(meta)
                if not uid_match or tt is None:
                    continue
                received = datetime.fromtimestamp(time.mktime(tt), tz=UTC)
                if received < since:
                    continue
                unread = b"\\Seen" not in imaplib.ParseFlags(meta)
                candidates.append(
                    (received, uid_match.group(1).decode(), unread, int(size_match.group(1)) if size_match else 0)
                )
            candidates.sort(reverse=True)
            total = len(candidates)
            messages: list[dict[str, Any]] = []
            for received, uid, unread, size in candidates[:max_messages]:
                section = "BODY.PEEK[HEADER]" if size > MAX_BODY_BYTES else "BODY.PEEK[]"
                status, data = conn.uid("fetch", uid, f"({section})")
                raw = next((item[1] for item in data or [] if isinstance(item, tuple)), b"")
                messages.append(_summarize_message(raw, uid=uid, unread=unread, received=received))
            return messages, total
        finally:
            with contextlib.suppress(OSError, imaplib.IMAP4.error):
                conn.logout()


def _fixture_messages(settings: Settings, max_messages: int, unread_only: bool) -> tuple[list[dict[str, Any]], int]:
    directory = settings.email_fixture_dir
    if not directory.is_dir():
        raise ToolError(f"Sample mailbox not found at {directory}")
    messages = []
    for path in sorted(directory.glob("*.eml")):
        raw = path.read_bytes()
        unread = b"X-Fixture-Unread: yes" in raw
        if unread_only and not unread:
            continue
        messages.append(_summarize_message(raw, uid=path.stem, unread=unread, received=None))
    messages.sort(key=lambda m: m["date"] or "", reverse=True)
    return messages[:max_messages], len(messages)


class FetchInput(BaseModel):
    since_hours: int | None = Field(None, ge=1, le=168, description="Look-back window; default 24h (72h on Mondays).")
    max_messages: int | None = Field(None, ge=1, le=200, description="Newest messages to return (default 60).")
    unread_only: bool | None = Field(None, description="Only unread messages (default false).")
    mailbox: str | None = Field(None, pattern=r"^[A-Za-z0-9 _./\[\]\-]{1,100}$", description="Default INBOX.")
    source: Literal["imap", "fixture"] | None = Field(None, description="imap (default) or the bundled sample mailbox.")


class FetchRecentEmails(Tool):
    name = "fetch_recent_emails"
    description = (
        "Fetch recent messages (sender, subject, date, short text snippet) from the owner's mailbox, read-only."
    )
    connector = "imap"
    Input = FetchInput
    risk = RiskLevel.MEDIUM  # personal data
    side_effects = SideEffect.READ
    network = True
    timeout_seconds = 90.0
    audit = AuditConfig(log_arguments=True, log_output=False)
    output_schema = {
        "type": "object",
        "required": ["window_label", "messages", "returned", "total"],
        "properties": {"messages": {"type": "array"}, "returned": {"type": "integer"}, "total": {"type": "integer"}},
    }

    def network_destination(self, arguments: dict[str, Any], settings: Settings) -> str | None:
        return None if arguments.get("source") == "fixture" else settings.email_imap_host

    def run(self, args: FetchInput, ctx: ToolContext) -> dict[str, Any]:
        hours = args.since_hours or auto_lookback_hours()
        until = datetime.now(UTC)
        since = until - timedelta(hours=hours)
        limit = args.max_messages or 60
        if args.source == "fixture":
            messages, total = _fixture_messages(ctx.settings, limit, bool(args.unread_only))
        else:
            messages, total = ImapMailbox(ctx.settings).fetch(
                mailbox=args.mailbox or "INBOX", since=since, max_messages=limit, unread_only=bool(args.unread_only)
            )
        local_since = since.astimezone()
        return {
            "window_label": f"last {hours}h (since {local_since:%a %d %b %H:%M})",
            "since": since.isoformat(),
            "until": until.isoformat(),
            "source": args.source or "imap",
            "total": total,
            "returned": len(messages),
            "truncated": total > len(messages),
            "unread": sum(1 for m in messages if m["unread"]),
            "messages": messages,
        }


def _sandbox_log(settings: Settings, name: str) -> Path:
    assert settings.sandbox_dir is not None
    settings.sandbox_dir.mkdir(parents=True, exist_ok=True)
    return settings.sandbox_dir / name


def _already_done(log: Path, key: str) -> dict[str, Any] | None:
    if not log.exists():
        return None
    for line in log.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("idempotency_key") == key:
            return dict(entry)
    return None


def _append(log: Path, entry: dict[str, Any]) -> None:
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


class SaveDigestInput(BaseModel):
    markdown: str = Field(min_length=1, max_length=200_000)
    dry_run: bool | None = None


class SaveDigestFile(Tool):
    name = "save_digest_file"
    description = "Save the digest as a dated Markdown file in the configured digest folder (AF_DIGEST_DIR)."
    connector = "local_files"
    Input = SaveDigestInput
    risk = RiskLevel.LOW
    side_effects = SideEffect.WRITE
    action_class = "local_file_write"
    permissions = ["tools:invoke", "tools:invoke:write"]

    def run(self, args: SaveDigestInput, ctx: ToolContext) -> dict[str, Any]:
        today = datetime.now().astimezone()
        assert ctx.settings.sandbox_dir is not None
        folder = ctx.settings.sandbox_dir / "digests" if args.dry_run else ctx.settings.digest_dir
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"email-digest-{today:%Y-%m-%d}.md"  # fixed name: no caller-controlled paths
        header = f"<!-- generated by Agent Factory execution {ctx.execution_id} at {today:%Y-%m-%d %H:%M %Z} -->\n"
        path.write_text(header + args.markdown.strip() + "\n", encoding="utf-8")
        return {
            "path": str(path),
            "bytes": path.stat().st_size,
            "dry_run": bool(args.dry_run),
            "summary_line": f"Saved {path.name}",
        }


class SendDigestInput(BaseModel):
    subject: str = Field(min_length=1, max_length=200, pattern=r"^[^\r\n]+$")
    markdown: str = Field(min_length=1, max_length=200_000)
    dry_run: bool | None = None


class SendDigestToSelf(Tool):
    name = "send_digest_to_self"
    description = "Email the digest to the mailbox owner (the configured address is the only possible recipient)."
    connector = "smtp"
    Input = SendDigestInput
    risk = RiskLevel.MEDIUM
    side_effects = SideEffect.WRITE
    action_class = "email_to_self"
    network = True
    timeout_seconds = 60.0
    permissions = ["tools:invoke", "tools:invoke:write"]
    audit = AuditConfig(log_arguments=False, log_output=True)

    def network_destination(self, arguments: dict[str, Any], settings: Settings) -> str | None:
        return None if arguments.get("dry_run") else settings.email_smtp_host

    def run(self, args: SendDigestInput, ctx: ToolContext) -> dict[str, Any]:
        log = _sandbox_log(ctx.settings, "outbox.jsonl" if args.dry_run else "sent_digests.jsonl")
        with _lock:
            previous = _already_done(log, ctx.idempotency_key)
        if previous:  # resumed or retried execution: never send twice
            return {**previous, "duplicate": True}
        digest = hashlib.sha256(args.markdown.encode()).hexdigest()[:16]
        if args.dry_run:
            entry = {
                "idempotency_key": ctx.idempotency_key,
                "sent_to": ctx.settings.email_address or "(not configured)",
                "subject": args.subject,
                "dry_run": True,
                "content_sha": digest,
                "bytes": len(args.markdown),
            }
            with _lock:
                _append(log, entry)
            return {**entry, "duplicate": False}
        address, password = _credentials(ctx.settings)
        message = EmailMessage()
        message["From"] = address
        message["To"] = address
        message["Subject"] = args.subject
        message["X-Agent-Factory-Execution"] = ctx.execution_id
        message.set_content(args.markdown)
        try:
            with smtplib.SMTP_SSL(
                ctx.settings.email_smtp_host,
                ctx.settings.email_smtp_port,
                context=ssl.create_default_context(),
                timeout=30,
            ) as smtp:
                smtp.login(address, password)
                smtp.send_message(message, from_addr=address, to_addrs=[address])
        except smtplib.SMTPAuthenticationError as exc:
            raise ToolError("SMTP login failed: check the app password") from exc
        except (OSError, smtplib.SMTPException) as exc:
            raise ToolError(f"Sending failed: {exc.__class__.__name__}", retryable=True) from exc
        entry = {
            "idempotency_key": ctx.idempotency_key,
            "sent_to": address,
            "subject": args.subject,
            "dry_run": False,
            "content_sha": digest,
            "sent_at": datetime.now(UTC).isoformat(),
        }
        with _lock:
            _append(log, entry)
        return {**entry, "duplicate": False}


class NotifyInput(BaseModel):
    title: str = Field(min_length=1, max_length=100, pattern=r"^[^\r\n]+$")
    message: str = Field(min_length=1, max_length=300)
    dry_run: bool | None = None


class NotifyDesktop(Tool):
    name = "notify_desktop"
    description = "Show a macOS desktop notification (no-op on other systems)."
    connector = "desktop"
    Input = NotifyInput
    risk = RiskLevel.LOW
    side_effects = SideEffect.WRITE
    action_class = "desktop_notification"
    timeout_seconds = 15.0
    permissions = ["tools:invoke", "tools:invoke:write"]

    def run(self, args: NotifyInput, ctx: ToolContext) -> dict[str, Any]:
        if args.dry_run:
            return {"delivered": False, "reason": "dry run"}
        if sys.platform != "darwin":
            return {"delivered": False, "reason": "notifications are only supported on macOS"}
        script = [
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
        ]
        # title/message are passed as argv, never interpolated into AppleScript source
        proc = subprocess.run(  # noqa: S603 - fixed executable, data passed as argv
            ["/usr/bin/osascript", *script, args.title, args.message[:300]],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        if proc.returncode != 0:
            raise ToolError(f"Notification failed: {proc.stderr.strip()[:200]}")
        return {"delivered": True}
