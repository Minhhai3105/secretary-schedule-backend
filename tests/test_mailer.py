"""Unit tests for SMTP configuration and HTML email rendering."""

from unittest import TestCase
from unittest.mock import MagicMock, patch

from app.services.mailer import (
    password_reset_mail_configured,
    send_email_message,
    smtp_configured,
)


class MailerTests(TestCase):
    def test_mailer_supports_ssl_465_and_starttls_587_configuration(self) -> None:
        ssl_env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "bot@example.com",
            "SMTP_PASSWORD": "secret-password",
            "APP_FRONTEND_URL": "http://localhost:5173",
        }
        with patch.dict("os.environ", ssl_env, clear=False), patch(
            "app.services.mailer.smtplib.SMTP_SSL"
        ) as mock_smtp_ssl_cls:
            ssl_instance = MagicMock()
            mock_smtp_ssl_cls.return_value.__enter__.return_value = ssl_instance
            self.assertTrue(smtp_configured())
            self.assertTrue(password_reset_mail_configured())
            send_email_message(
                to_email="recipient@example.com",
                subject="Test SSL Reminder",
                body="SSL Reminder body",
            )
            mock_smtp_ssl_cls.assert_called_once()
            ssl_instance.login.assert_called_once_with(
                "bot@example.com", "secret-password"
            )
            ssl_instance.send_message.assert_called_once()

        starttls_env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
            "SMTP_USERNAME": "bot@example.com",
            "SMTP_PASSWORD": "secret-password",
            "APP_FRONTEND_URL": "http://localhost:5173",
        }
        with patch.dict("os.environ", starttls_env, clear=False), patch(
            "app.services.mailer.smtplib.SMTP"
        ) as mock_smtp_cls:
            smtp_instance = MagicMock()
            mock_smtp_cls.return_value.__enter__.return_value = smtp_instance
            self.assertTrue(smtp_configured())
            self.assertTrue(password_reset_mail_configured())
            send_email_message(
                to_email="recipient@example.com",
                subject="Test STARTTLS Reminder",
                body="STARTTLS Reminder body",
            )
            smtp_instance.starttls.assert_called_once()
            smtp_instance.login.assert_called_once_with(
                "bot@example.com", "secret-password"
            )
            smtp_instance.send_message.assert_called_once()

    def test_mailer_delivers_multipart_alternative_with_html_body(self) -> None:
        ssl_env = {
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "bot@example.com",
            "SMTP_PASSWORD": "secret-password",
        }
        with patch.dict("os.environ", ssl_env, clear=False), patch(
            "app.services.mailer.smtplib.SMTP_SSL"
        ) as mock_smtp_ssl_cls:
            ssl_instance = MagicMock()
            mock_smtp_ssl_cls.return_value.__enter__.return_value = ssl_instance
            send_email_message(
                to_email="user@example.com",
                subject="Test HTML Reminder",
                body="Plain text reminder",
                html_body="<p>HTML formatted reminder</p>",
            )
            self.assertEqual(ssl_instance.send_message.call_count, 1)
            msg = ssl_instance.send_message.call_args[0][0]
            self.assertTrue(msg.is_multipart())
            payloads = msg.get_payload()
            self.assertEqual(len(payloads), 2)
            self.assertEqual(payloads[0].get_content_type(), "text/plain")
            self.assertIn("Plain text reminder", payloads[0].get_payload())
            self.assertEqual(payloads[1].get_content_type(), "text/html")
            self.assertIn("<p>HTML formatted reminder</p>", payloads[1].get_payload())

    def test_email_templates_escape_html_against_xss(self) -> None:
        from app.services.email_template import (
            render_daily_summary_html,
            render_near_reminder_html,
            render_password_reset_html,
            render_update_notification_html,
        )

        xss_payload = "<script>alert('xss')</script>"
        html_near = render_near_reminder_html(
            title=xss_payload,
            starts_at="2026-11-12 10:00",
            ends_at=None,
            meeting_mode="offline",
            related_person=xss_payload,
            location=xss_payload,
            preparation=xss_payload,
            notes=xss_payload,
        )
        self.assertNotIn("<script>", html_near)
        self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html_near)

        html_daily = render_daily_summary_html(
            date_str="2026-11-12",
            schedules=[{
                "title": xss_payload,
                "time_range": "10:00 - 11:00",
                "mode": "offline",
                "related_person": xss_payload,
            }],
        )
        self.assertNotIn("<script>", html_daily)
        self.assertIn("&lt;script&gt;", html_daily)

        html_update = render_update_notification_html(
            title=xss_payload,
            version=2,
            starts_at="10:00",
            ends_at="11:00",
            meeting_mode="offline",
            related_person=xss_payload,
            is_cancelled=False,
        )
        self.assertNotIn("<script>", html_update)
        self.assertIn("&lt;script&gt;", html_update)

        html_reset = render_password_reset_html(
            reset_url="http://localhost:5173/reset-password?token=123\"onclick=\"alert(1)"
        )
        self.assertNotIn("\"onclick=", html_reset)
        self.assertIn("&quot;onclick=", html_reset)
        self.assertNotIn("cài đặt nhắc việc", html_reset)
        self.assertIn("Email bảo mật", html_reset)
