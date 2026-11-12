import logging
import smtplib
import ssl
from email.message import EmailMessage
from os import environ
from urllib.parse import quote, urlsplit

from app.services.email_template import render_password_reset_html

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


def _frontend_base_url() -> str | None:
    value = environ.get("APP_FRONTEND_URL", "").strip()
    if not value or any(character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(value)
        valid_port = parsed.port is None or parsed.port > 0
    except ValueError:
        return None
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.hostname
        or not valid_port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    return value.rstrip("/")


def password_reset_mail_configured() -> bool:
    return smtp_configured() and _frontend_base_url() is not None


def send_email_message(
    *,
    to_email: str,
    subject: str,
    body: str,
    html_body: str | None = None,
) -> None:
    if not smtp_configured():
        raise RuntimeError("SMTP is not configured")

    host = environ["SMTP_HOST"].strip()
    port = int(environ.get("SMTP_PORT", "465"))
    timeout = int(environ.get("SMTP_TIMEOUT_SECONDS", "30"))
    username = environ.get("SMTP_USERNAME", "").strip()
    password = environ.get("SMTP_PASSWORD", "")
    if host.lower() == "smtp.gmail.com":
        password = password.replace(" ", "")
    sender = (environ.get("SMTP_FROM") or username).strip()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_email
    message.set_content(body)
    if html_body:
        message.add_alternative(html_body, subtype="html")

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
            timeout=timeout,
        ) as smtp:
            if username:
                smtp.login(username, password)
            smtp.send_message(message)
    else:
        with smtplib.SMTP(host, port, timeout=timeout) as smtp:
            if use_starttls:
                smtp.starttls(context=ssl.create_default_context())
            if username:
                smtp.login(username, password)
            smtp.send_message(message)


def send_password_reset_email(to_email: str, raw_token: str) -> None:
    frontend_url = _frontend_base_url()
    if frontend_url is None:
        raise RuntimeError("APP_FRONTEND_URL must be an HTTP(S) frontend URL")
    reset_url = (
        frontend_url
        + "/reset-password?token="
        + quote(raw_token, safe="")
    )
    body = (
        "Open this link within 30 minutes to set a new password:\n\n"
        + reset_url
        + "\n\nIf you did not request this, ignore this email."
    )
    html_body = render_password_reset_html(reset_url=reset_url)
    try:
        send_email_message(
            to_email=to_email,
            subject="Reset your Secretary Schedule password",
            body=body,
            html_body=html_body,
        )
    except Exception as exc:
        logger.error("Password reset email failed: %s", type(exc).__name__)
