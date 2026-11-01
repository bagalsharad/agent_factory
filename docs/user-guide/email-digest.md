# Daily email digest — setup

## 1. Gmail app password (one time)

1. Turn on 2-Step Verification for your Google account.
2. Create an app password at <https://myaccount.google.com/apppasswords> (name it "Agent Factory").
3. Make sure IMAP is enabled in Gmail settings → *Forwarding and POP/IMAP*.

## 2. Configure Agent Factory (from the project directory)

```bash
uv run agent-factory email setup            # asks for your address and the app password
```

This stores the app password in the **macOS Keychain** (service `agent-factory`, item `email_app_password`) and
writes only non-secret settings to `./.env`:

```
AF_EMAIL_ADDRESS=you@gmail.com
AF_EMAIL_IMAP_HOST=imap.gmail.com
AF_EMAIL_SMTP_HOST=smtp.gmail.com
AF_SECRETS_BACKEND=keyring
AF_NETWORK_ALLOWLIST=imap.gmail.com,smtp.gmail.com
```

It then logs in read-only and lists the newest messages (`uv run agent-factory email test` re-runs that check).
Optional: `AF_DIGEST_DIR=~/Documents/EmailDigests` (default).

## 3. Local model (email never leaves your Mac)

```bash
ollama pull qwen3:8b
```

## 4. Try it

```bash
uv run agent-factory agents run email-digest -i '{"source": "fixture", "dry_run": true}'   # sample mailbox, no delivery
uv run agent-factory agents run email-digest -i '{"dry_run": true}'                        # your inbox, digest saved under data/sandbox
uv run agent-factory agents run email-digest -i '{}'                                       # real run: file + email + notification
```

Inputs (all optional): `since_hours` (default 24, or 72 on Mondays), `max_messages` (default 60), `unread_only`,
`source` (`imap`/`fixture`), `dry_run`.

## 5. Schedule it (weekdays 07:00)

```bash
uv run agent-factory schedule install email-digest --at 07:00 --days weekdays
uv run agent-factory schedule list
launchctl kickstart gui/$(id -u)/com.agentfactory.email-digest     # run now
uv run agent-factory schedule remove email-digest
```

Logs: `~/Library/Logs/AgentFactory/email-digest.log`. Each run also appears in the dashboard's Execution Monitor.
If the Mac is asleep at 07:00, launchd runs the job when it wakes. The server does not need to be running.

## Privacy and safety

- Mailbox access is read-only; messages stay unread.
- The digest can only be emailed to `AF_EMAIL_ADDRESS`; the send tool has no recipient parameter, and subjects
  containing line breaks (header injection) are rejected.
- The workflow is deterministic: the model only writes text. Instructions hidden in emails ("forward all mail to…")
  cannot select or call tools. Suspicious messages are listed under *Suspicious*.
- Email text is excluded from execution event logs (`capture_content: false`). Fetched snippets are kept in the local
  database for checkpoint recovery and purged after `AF_RETENTION_DAYS` (default 30).
- Using a cloud model instead (e.g. Claude) would send email content to that provider — keep Ollama for private mail.
