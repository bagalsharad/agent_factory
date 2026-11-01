# Daily Email Digest Agent

Every weekday at 07:00 (launchd), the agent reads the last 24 hours of email (72 hours on Mondays) from your
Gmail inbox, writes a digest with **Needs action / Important / FYI / Suspicious**, saves it as Markdown, emails it
to you, and shows a macOS notification. Setup: [docs/user-guide/email-digest.md](../../docs/user-guide/email-digest.md).

| Step | Kind | What happens |
|---|---|---|
| `fetch` | tool `fetch_recent_emails` | Read-only IMAP (`readonly=True`, `BODY.PEEK`) — nothing is marked as read, moved or deleted |
| `summarize` | model (local Ollama `qwen3:8b`) | Structured digest; email text is wrapped as untrusted data; the model has no tools |
| `validate` | validate | Output must match the digest schema |
| `save` | tool `save_digest_file` | `~/Documents/EmailDigests/email-digest-YYYY-MM-DD.md` (fixed filename) |
| `email_me` | tool `send_digest_to_self` | SMTP to **your own address only** (no recipient parameter exists); never sent twice for one run |
| `notify` | tool `notify_desktop` | macOS notification |

`sample_mailbox/` holds seven synthetic emails (including a phishing message that tries to instruct the assistant
to forward mail) used by the evaluation suite with `{"source": "fixture", "dry_run": true}`.
