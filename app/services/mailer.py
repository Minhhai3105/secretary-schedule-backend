import logging
import smtplib
import ssl
from email.message import EmailMessage
from os import environ
from urllib.parse import quote

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    host = environ.get("SMTP_HOST", "").strip()
    sender = (environ.get("SMTP_FROM") or environ.get("SMTP_USERNAME") or "").strip()
    if not host or not sender:
        return False
    username = environ.get("SMTP_USERNAME", "").strip()
    password = environ.get("SMTP_PASSWORD", "")
    if username and not password:
        return False
    return True


def password_reset_mail_configured() -> bool:
    return smtp_configured() and bool(environ.get("APP_FRONTEND_URL", "").strip())


def send_email_message(*, to_email: str, subject: str, body: str) -> None:
    if not smtp_configured():
        raise RuntimeError("SMTP is not configured")

    host = environ["SMTP_HOST"].strip()
    port = int(environ.get("SMTP_PORT", "465"))
    username = environ.get("SMTP_USERNAME", "").strip()
    password = environ.get("SMTP_PASSWORD", "")
    sender = (environ.get("SMTP_FROM") or username).strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)

    use_ssl = port == 465 or environ.get("SMTP_SSL", "").lower() in ("1", "true")
    use_starttls = port == 587 or environ.get("SMTP_STARTTLS", "").lower() in (
        "1",
        "true",
    )

    if use_ssl:
        with smtplib.SMTP_SSL(
            host,
            port,
            context=ssl.create_default_context(),
            timeout=10,
        ) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            if use_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password)
            smtp.send_message(message)


def send_password_reset_email(to_email: str, raw_token: str) -> None:
    reset_url = (
        environ["APP_FRONTEND_URL"].rstrip("/")
        + "/reset-password?token="
        + quote(raw_token, safe="")
    )
    body = (
        "Open this link within 30 minutes to set a new password:\n\n"
        + reset_url
        + "\n\nIf you did not request this, ignore this email."
    )
    try:
        send_email_message(
            to_email=to_email,
            subject="Reset your Secretary Schedule password",
            body=body,
        )
    except Exception as exc:
        logger.error("Password reset email failed: %s", type(exc).__name__)
