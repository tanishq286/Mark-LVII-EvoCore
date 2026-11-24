"""plugins/email_automator.py — SMTP/IMAP email draft, CONFIRM-gated send w/ attachments, and inbox summary."""

from __future__ import annotations

import email
from email.header import decode_header
from email.message import EmailMessage
import imaplib
import mimetypes
from pathlib import Path
import smtplib

from core.env_loader import get_env, missing_env

PLUGIN = {
    "name": "email_automator",
    "brief": "Draft, inspect IMAP inbox summaries, and CONFIRM-gated send emails with attachments",
    "description": (
        "Automates email workflows using credentials from core.env_loader (EMAIL_ADDRESS, EMAIL_PASSWORD, "
        "optional SMTP_HOST/SMTP_PORT/IMAP_HOST). Drafts and summarizes recent IMAP emails wrapped safely "
        "with <<<UNTRUSTED>>> markers, and enforces CONFIRM gating on outbound send with attachments. "
        "Actions: status, draft, inbox_summary, send."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "action": {
                "type": "STRING",
                "description": "Action: status | draft | inbox_summary | send (default: status)",
            },
            "to": {
                "type": "STRING",
                "description": "Recipient email address.",
            },
            "subject": {
                "type": "STRING",
                "description": "Email subject line.",
            },
            "body": {
                "type": "STRING",
                "description": "Email plain-text body.",
            },
            "attachments": {
                "type": "ARRAY",
                "items": {"type": "STRING"},
                "description": "Optional list of local file paths to attach.",
            },
            "limit": {
                "type": "INTEGER",
                "description": "Number of recent inbox messages for inbox_summary (default: 5).",
            },
        },
        "required": [],
    },
}


def _decode_mime_header(raw: str | None) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    out = []
    for chunk, enc in parts:
        if isinstance(chunk, bytes):
            out.append(chunk.decode(enc or "utf-8", errors="ignore"))
        else:
            out.append(str(chunk))
    return "".join(out).strip()


def _validate_attachments(paths: list | None) -> tuple[list[Path], str]:
    files: list[Path] = []
    for raw in (paths or []):
        p = Path(str(raw))
        if not p.is_file() or p.is_symlink():
            return [], f"Invalid or missing attachment path: {p}"
        files.append(p)
    return files, ""


def build_email_message(
    *,
    sender: str,
    to_addr: str,
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to_addr
    msg["Subject"] = subject
    msg.set_content(body)
    for fp in (attachments or []):
        ctype, encoding = mimetypes.guess_type(str(fp))
        if ctype is None or encoding is not None:
            ctype = "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(fp.read_bytes(), maintype=maintype, subtype=subtype, filename=fp.name)
    return msg


def _send_smtp(to_addr: str, subject: str, body: str, attachments: list[Path]) -> str:
    missing = missing_env(("EMAIL_ADDRESS", "EMAIL_PASSWORD"))
    if missing:
        return "Email credentials missing (" + ", ".join(missing) + ")."
    sender = get_env("EMAIL_ADDRESS")
    password = get_env("EMAIL_PASSWORD")
    host = get_env("SMTP_HOST", "smtp.gmail.com")
    port = int(get_env("SMTP_PORT", "587") or 587)

    msg = build_email_message(
        sender=sender, to_addr=to_addr, subject=subject, body=body, attachments=attachments
    )
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        server.login(sender, password)
        server.send_message(msg)
    return f"Email sent via {host} to {to_addr} (attachments={len(attachments)})."


def inbox_summary(*, limit: int = 5) -> str:
    missing = missing_env(("EMAIL_ADDRESS", "EMAIL_PASSWORD"))
    if missing:
        return "Email credentials missing (" + ", ".join(missing) + ")."
    addr = get_env("EMAIL_ADDRESS")
    pwd = get_env("EMAIL_PASSWORD")
    host = get_env("IMAP_HOST", "imap.gmail.com")
    port = int(get_env("IMAP_PORT", "993") or 993)

    cap = max(1, min(15, int(limit or 5)))
    items: list[str] = ["<<<UNTRUSTED>>>", f"Inbox Summary (latest {cap}):"]
    with imaplib.IMAP4_SSL(host, port) as imap:
        imap.login(addr, pwd)
        imap.select("INBOX", readonly=True)
        typ, data = imap.search(None, "ALL")
        if typ != "OK" or not data or not data[0]:
            return "Inbox is empty."
        ids = data[0].split()[-cap:][::-1]
        for mid in ids:
            t2, msg_data = imap.fetch(mid, "(RFC822.HEADER)")
            if t2 != "OK" or not msg_data:
                continue
            raw_hdr = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
            parsed = email.message_from_bytes(raw_hdr)
            frm = _decode_mime_header(parsed.get("From"))
            subj = _decode_mime_header(parsed.get("Subject")) or "(no subject)"
            dt = parsed.get("Date") or ""
            items.append(f"- From: {frm} | Subject: {subj} | Date: {dt}")
    items.append("<<<END_UNTRUSTED>>>")
    return "\n".join(items)


def run(parameters: dict, player=None, session_memory=None) -> str:
    """Plugin entrypoint for SMTP/IMAP email automation."""
    params = parameters if isinstance(parameters, dict) else {}
    action = str(params.get("action") or "status").strip().lower()

    if action == "status":
        missing = missing_env(("EMAIL_ADDRESS", "EMAIL_PASSWORD"))
        if missing:
            return "Email Automator: unconfigured (missing " + ", ".join(missing) + ")."
        return f"Email Automator ready (smtp={get_env('SMTP_HOST', 'smtp.gmail.com')}, imap={get_env('IMAP_HOST', 'imap.gmail.com')})."

    if action == "draft":
        to_addr = str(params.get("to") or "").strip()
        subject = str(params.get("subject") or "(no subject)").strip()
        body = str(params.get("body") or "").strip()
        files, err = _validate_attachments(params.get("attachments"))
        if err:
            return err
        return (
            f"Draft ready:\nTo: {to_addr}\nSubject: {subject}\n"
            f"Attachments: {[f.name for f in files]}\n---\n{body}"
        )

    if action in ("inbox_summary", "inbox", "list"):
        return inbox_summary(limit=int(params.get("limit") or 5))

    if action == "send":
        to_addr = str(params.get("to") or "").strip()
        subject = str(params.get("subject") or "").strip()
        body = str(params.get("body") or "").strip()
        if not to_addr or not subject:
            return "Provide 'to' and 'subject' to send an email."
        files, err = _validate_attachments(params.get("attachments"))
        if err:
            return err
        from core.evolution import safety_guard
        detail = (
            f"To: {to_addr} | Subject: {subject} | Attachments: {[f.name for f in files]} "
            f"| Preview: {body[:80]}"
        )
        return safety_guard.enforce_gate(
            "email_automator",
            "send",
            f"Send Email to {to_addr}",
            detail,
            lambda: _send_smtp(to_addr, subject, body, files),
            key=f"email_send_{to_addr[:24]}",
        )

    return f"Unknown email_automator action '{action}'. Use: status, draft, inbox_summary, send."
