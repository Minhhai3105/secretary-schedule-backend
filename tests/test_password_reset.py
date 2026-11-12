"""PostgreSQL-backed password reset flow and session revocation tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import BackgroundTasks, HTTPException

from app.api.password_reset import forgot_password, reset_password
from app.core.security import verify_password
from app.models.auth_session import AuthSession
from app.models.refresh_token import RefreshToken
from app.schemas.password_reset import ForgotPasswordRequest, ResetPasswordRequest
from app.services.mailer import (
    password_reset_mail_configured,
    send_password_reset_email,
    smtp_configured,
)
from tests._db_case import DatabaseCase


class PasswordResetTests(DatabaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.user.email = self.user.email.replace("@example.invalid", "@example.com")
        self.db.flush()

    def test_forgot_password_returns_503_when_smtp_unconfigured(self) -> None:
        with patch.dict("os.environ", {"SMTP_HOST": ""}, clear=False):
            with self.assertRaises(HTTPException) as ctx:
                forgot_password(
                    ForgotPasswordRequest(email=self.user.email),
                    BackgroundTasks(),
                    self.db,
                )
            self.assertEqual(ctx.exception.status_code, 503)

    def test_password_reset_requires_valid_frontend_url_and_builds_link(self) -> None:
        smtp_env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_USERNAME": "noreply@example.com",
            "SMTP_PASSWORD": "test-only",
        }
        invalid_urls = (
            "", "example.com", "ftp://example.com", "https://example.com?x=1",
            "https://example.com:bad", "https://user:pass@example.com",
        )
        for frontend_url in invalid_urls:
            with self.subTest(frontend_url=frontend_url):
                with patch.dict("os.environ", {**smtp_env, "APP_FRONTEND_URL": frontend_url}):
                    self.assertTrue(smtp_configured())
                    self.assertFalse(password_reset_mail_configured())

        with patch.dict(
            "os.environ", {**smtp_env, "APP_FRONTEND_URL": "http://localhost:5173/"},
        ), patch("app.services.mailer.send_email_message") as send:
            self.assertTrue(password_reset_mail_configured())
            send_password_reset_email("user@example.com", "token with spaces")
            self.assertIn(
                "http://localhost:5173/reset-password?token=token%20with%20spaces",
                send.call_args.kwargs["body"],
            )

    def test_forgot_and_reset_password_updates_hash_and_revokes_sessions(self) -> None:
        now = datetime.now(timezone.utc)
        session = AuthSession(
            user_id=self.user.id,
            created_at_utc=now,
            expires_at_utc=now + timedelta(days=7),
        )
        self.db.add(session)
        self.db.flush()

        refresh = RefreshToken(
            session_id=session.id,
            token_hash="a" * 64,
            created_at_utc=now,
            expires_at_utc=now + timedelta(days=7),
        )
        self.db.add(refresh)
        self.db.flush()

        tasks = BackgroundTasks()
        env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "noreply@example.com",
            "SMTP_PASSWORD": "secret",
            "APP_FRONTEND_URL": "http://localhost:5173",
        }
        with patch.dict("os.environ", env, clear=False):
            response = forgot_password(
                ForgotPasswordRequest(email=self.user.email),
                tasks,
                self.db,
            )
        self.assertEqual(len(tasks.tasks), 1)
        raw_token = tasks.tasks[0].args[1]
        self.assertTrue(raw_token)
        self.assertIn("reset link", response.message)

        new_password = "NewSecurePassword!987"
        reset_password(
            ResetPasswordRequest(token=raw_token, password=new_password),
            self.db,
        )

        self.db.refresh(self.user)
        self.db.refresh(session)
        self.db.refresh(refresh)
        self.assertTrue(verify_password(new_password, self.user.password_hash))
        self.assertIsNotNone(session.revoked_at_utc)
        self.assertIsNotNone(refresh.revoked_at_utc)

        with self.assertRaises(HTTPException) as replay:
            reset_password(
                ResetPasswordRequest(token=raw_token, password="AnotherPassword!123"),
                self.db,
            )
        self.assertEqual(replay.exception.status_code, 400)
