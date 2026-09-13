"""SMTP delivery.

Settings live in the admin console, not in code, so the client can point
the system at their own mail server without a redeployment.  Every send
is logged; a failure is recorded and returned, never raised, because a
mail server being down must not stop someone saving a figure.
"""
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr

from models import db, NotificationLog, get_setting


def config():
    return {
        "host": get_setting("smtp_host", ""),
        "port": int(get_setting("smtp_port", "587") or 587),
        "user": get_setting("smtp_user", ""),
        "password": get_setting("smtp_pass", ""),
        "from_addr": get_setting("smtp_from", ""),
        "from_name": get_setting("smtp_from_name", get_setting("org_name", "Cash Flow")),
        "tls": get_setting("smtp_tls", "1") in ("1", "true", "True", "yes", "on"),
    }


def configured():
    c = config()
    return bool(c["host"] and c["from_addr"])


def send(to_addr, subject, html, text=None, timeout=20):
    """Send one message. Returns (ok, message) — never raises."""
    if not to_addr:
        return False, "No email address on file"
    c = config()
    if not configured():
        return False, "Email is not configured — set the mail server in the admin console"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((c["from_name"], c["from_addr"]))
    msg["To"] = to_addr
    msg.set_content(text or _strip(html))
    msg.add_alternative(html, subtype="html")

    try:
        if c["port"] == 465:
            with smtplib.SMTP_SSL(c["host"], c["port"], timeout=timeout,
                                  context=ssl.create_default_context()) as s:
                if c["user"]:
                    s.login(c["user"], c["password"])
                s.send_message(msg)
        else:
            with smtplib.SMTP(c["host"], c["port"], timeout=timeout) as s:
                if c["tls"]:
                    s.starttls(context=ssl.create_default_context())
                if c["user"]:
                    s.login(c["user"], c["password"])
                s.send_message(msg)
        return True, f"Sent to {to_addr}"
    except Exception as exc:                       # noqa: BLE001 - reported, not raised
        return False, f"{type(exc).__name__}: {exc}"


def send_and_log(to_addr, subject, html, kind, week_id=None, triggered_by=""):
    ok, message = send(to_addr, subject, html)
    try:
        db.session.add(NotificationLog(
            kind=kind, week_id=week_id, recipient=to_addr or "", subject=subject[:300],
            ok=ok, error=(None if ok else message[:500]), triggered_by=triggered_by))
        db.session.commit()
    except Exception:
        db.session.rollback()
    return ok, message


def _strip(html):
    import re
    text = re.sub(r"<br\s*/?>", "\n", html or "")
    text = re.sub(r"</(p|div|tr|h1|h2|h3|li)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
