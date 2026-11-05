import logging
import smtplib
import ssl
from email.message import EmailMessage
from os import environ
from urllib.parse import quote

logger = logging.getLogger(__name__)


def password_reset_mail_configured() -> bool:
    return all(
        environ.get(key)
        for key in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "APP_FRONTEND_URL")
    )


def send_password_reset_email(to_email: str, raw_token: str) -> None:
    reset_url = (
        environ["APP_FRONTEND_URL"].rstrip("/")
        + "/reset-password?token="
        + quote(raw_token, safe="")
    )
    message = EmailMessage()
    message["Subject"] = "Reset your Secretary Schedule password"
    message["From"] = environ.get("SMTP_FROM") or environ["SMTP_USERNAME"]
    message["To"] = to_email
    message.set_content(
        "Open this link within 30 minutes to set a new password:\n\n"
        + reset_url
        + "\n\nIf you did not request this, ignore this email."
    )

    try:
        with smtplib.SMTP_SSL(
            environ["SMTP_HOST"],
            int(environ.get("SMTP_PORT", "465")),
            context=ssl.create_default_context(),
            timeout=10,
        ) as smtp:
            smtp.login(environ["SMTP_USERNAME"], environ["SMTP_PASSWORD"])
            smtp.send_message(message)
    except Exception as exc:
        logger.error("Password reset email failed: %s", type(exc).__name__)
