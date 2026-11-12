"""PostgreSQL-backed email reminder worker and retry tests."""

from datetime import datetime, time, timedelta, timezone
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.api.email_deliveries import get_email_delivery, list_email_deliveries
from app.api.schedules import cancel_schedule, create_schedule, update_schedule
from app.models.email_attempt import EmailAttempt
from app.models.email_delivery import EmailDelivery
from app.models.reminder_setting import ReminderSetting
from app.schemas.schedule import ScheduleCancel, ScheduleCreate, ScheduleUpdate
from app.services.mailer import (
    password_reset_mail_configured,
    send_email_message,
    smtp_configured,
)
from app.services.reminders import (
    MAX_ATTEMPTS,
    enqueue_due_reminders,
    process_due_deliveries,
)
from tests._db_case import DatabaseCase

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


class ReminderWorkerTests(DatabaseCase):
    def setUp(self) -> None:
        super().setUp()
        self.settings = ReminderSetting(
            user_id=self.user.id,
            daily_enabled=True,
            daily_time_vietnam=time(7, 0),
            near_enabled=True,
            lead_minutes=30,
        )
        self.db.add(self.settings)
        self.db.flush()

    @staticmethod
    def _relative_morning_utc() -> datetime:
        target_date = datetime.now(VIETNAM_TZ).date() + timedelta(days=1)
        return datetime.combine(
            target_date, time(8, 0), tzinfo=VIETNAM_TZ
        ).astimezone(timezone.utc)

    def test_daily_and_near_reminders_enqueued_and_sent_once(self) -> None:
        now = self._relative_morning_utc()
        schedule = create_schedule(
            ScheduleCreate(
                title="Morning briefing",
                starts_at_utc=now + timedelta(minutes=20),
                meeting_mode="online",
                meeting_url="https://meet.example.com/briefing",
            ),
            self.user,
            self.db,
        )

        sent_messages: list[dict[str, str]] = []

        def fake_send(*, to_email: str, subject: str, body: str) -> None:
            sent_messages.append(
                {"to": to_email, "subject": subject, "body": body}
            )

        created = enqueue_due_reminders(self.db, now_utc=now)
        self.assertEqual(len(created), 2)
        self.assertEqual(len(enqueue_due_reminders(self.db, now_utc=now)), 0)

        processed = process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=fake_send,
        )
        self.assertEqual(len(processed), 2)
        self.assertEqual(len(sent_messages), 2)

        page = list_email_deliveries(
            from_date=None,
            to_date=None,
            delivery_status="sent",
            kind=None,
            page=1,
            user=self.user,
            db=self.db,
        )
        self.assertEqual(page.paging.total, 2)
        kinds = {item.kind for item in page.deliveries}
        self.assertEqual(kinds, {"daily", "near"})
        near_item = next(item for item in page.deliveries if item.kind == "near")
        self.assertEqual(near_item.schedule_id, schedule.id)

    def test_failed_delivery_retries_up_to_four_attempts_and_records_reason(self) -> None:
        now = self._relative_morning_utc()
        self.settings.near_enabled = False
        self.db.flush()

        enqueue_due_reminders(self.db, now_utc=now)

        def failing_send(*, to_email: str, subject: str, body: str) -> None:
            raise ConnectionError("SMTP connection refused")

        for step in range(MAX_ATTEMPTS + 2):
            process_due_deliveries(
                self.db,
                now_utc=now + timedelta(minutes=step * 5),
                retry_delay=timedelta(minutes=5),
                send_fn=failing_send,
            )

        delivery = self.db.scalar(
            select(EmailDelivery).where(EmailDelivery.user_id == self.user.id)
        )
        self.assertIsNotNone(delivery)
        detail = get_email_delivery(delivery.id, self.user, self.db)
        self.assertEqual(detail.status, "failed")
        self.assertEqual(len(detail.attempts), MAX_ATTEMPTS)
        self.assertEqual(
            [item.attempt_number for item in detail.attempts], [1, 2, 3, 4]
        )
        self.assertIn(
            "ConnectionError: SMTP connection refused",
            detail.attempts[-1].error_message or "",
        )

    def test_cancelled_or_updated_schedule_cancels_stale_near_and_enqueues_update(
        self,
    ) -> None:
        now = datetime.now(timezone.utc)
        self.settings.daily_enabled = False
        self.db.flush()

        schedule = create_schedule(
            ScheduleCreate(
                title="Partner sync",
                starts_at_utc=now + timedelta(minutes=15),
                meeting_mode="offline",
                location="Room 302",
            ),
            self.user,
            self.db,
        )
        enqueue_due_reminders(self.db, now_utc=now)

        update_schedule(
            schedule.id,
            ScheduleUpdate(version=1, title="Partner sync (rescheduled)"),
            self.user,
            self.db,
        )
        cancel_schedule(
            schedule.id,
            ScheduleCancel(version=2),
            self.user,
            self.db,
        )

        enqueue_due_reminders(self.db, now_utc=now)
        deliveries = self.db.scalars(
            select(EmailDelivery)
            .where(EmailDelivery.user_id == self.user.id)
            .order_by(EmailDelivery.kind)
        ).all()
        by_kind = {item.kind: item for item in deliveries}
        self.assertEqual(by_kind["near"].status, "cancelled")
        self.assertEqual(by_kind["update"].status, "pending")
        self.assertEqual(by_kind["update"].schedule_id, schedule.id)

    def test_consecutive_updates_v1_v2_v3_only_send_v3_notification(self) -> None:
        now = datetime.now(timezone.utc)
        self.settings.daily_enabled = False
        self.settings.near_enabled = True
        self.db.flush()

        # 1) Update v1 -> v2 -> v3 before worker runs at all: only v3 is enqueued and sent
        schedule_fast = create_schedule(
            ScheduleCreate(
                title="Quick sync v1",
                starts_at_utc=now + timedelta(hours=4),
                meeting_mode="online",
                meeting_url="https://meet.example.com/quick",
            ),
            self.user,
            self.db,
        )
        update_schedule(
            schedule_fast.id,
            ScheduleUpdate(version=1, title="Quick sync v2"),
            self.user,
            self.db,
        )
        update_schedule(
            schedule_fast.id,
            ScheduleUpdate(version=2, title="Quick sync v3"),
            self.user,
            self.db,
        )

        fast_sent: list[str] = []
        enqueue_due_reminders(self.db, now_utc=now)
        process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=lambda **kw: fast_sent.append(kw["subject"]),
        )
        fast_updates = self.db.scalars(
            select(EmailDelivery).where(
                EmailDelivery.schedule_id == schedule_fast.id,
                EmailDelivery.kind == "update",
            )
        ).all()
        self.assertEqual([d.event_key for d in fast_updates], [f"update:{schedule_fast.id}:v3"])
        self.assertEqual(fast_updates[0].status, "sent")
        self.assertEqual(len(fast_sent), 1)
        self.assertIn("Quick sync v3", fast_sent[0])

        # 2) Existing pending/failed v2 update is cancelled when schedule advances to v3,
        # and version is re-verified immediately before sending
        schedule = create_schedule(
            ScheduleCreate(
                title="Roadmap review v1",
                starts_at_utc=now + timedelta(hours=3),
                meeting_mode="online",
                meeting_url="https://meet.example.com/roadmap",
            ),
            self.user,
            self.db,
        )
        update_schedule(
            schedule.id,
            ScheduleUpdate(version=1, title="Roadmap review v2"),
            self.user,
            self.db,
        )
        enqueue_due_reminders(self.db, now_utc=now)

        def fail_once(*, to_email: str, subject: str, body: str) -> None:
            raise ConnectionError("Temporary SMTP failure on v2")

        process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=fail_once,
        )

        update_schedule(
            schedule.id,
            ScheduleUpdate(version=2, title="Roadmap review v3"),
            self.user,
            self.db,
        )

        sent_subjects: list[str] = []

        def record_send(*, to_email: str, subject: str, body: str) -> None:
            sent_subjects.append(subject)

        enqueue_due_reminders(self.db, now_utc=now + timedelta(minutes=1))
        process_due_deliveries(
            self.db,
            now_utc=now + timedelta(minutes=1),
            retry_delay=timedelta(0),
            send_fn=record_send,
        )

        update_deliveries = self.db.scalars(
            select(EmailDelivery)
            .where(
                EmailDelivery.schedule_id == schedule.id,
                EmailDelivery.kind == "update",
            )
            .order_by(EmailDelivery.event_key)
        ).all()
        by_key = {item.event_key: item.status for item in update_deliveries}
        self.assertEqual(by_key[f"update:{schedule.id}:v2"], "cancelled")
        self.assertEqual(by_key[f"update:{schedule.id}:v3"], "sent")
        self.assertEqual(len(sent_subjects), 1)
        self.assertIn("Roadmap review v3", sent_subjects[0])

        # 3) Version check right before sending in process_due_deliveries cancels stale pending delivery
        stale_pending = EmailDelivery(
            user_id=self.user.id,
            schedule_id=schedule.id,
            event_key=f"update:{schedule.id}:v2-stale-direct",
            kind="update",
            applicable_date_vietnam=now.astimezone(VIETNAM_TZ).date(),
            due_at_utc=now,
            status="pending",
        )
        self.db.add(stale_pending)
        self.db.flush()
        process_due_deliveries(
            self.db,
            now_utc=now + timedelta(minutes=2),
            retry_delay=timedelta(0),
            send_fn=record_send,
        )
        self.db.refresh(stale_pending)
        self.assertEqual(stale_pending.status, "cancelled")
        self.assertEqual(len(sent_subjects), 1)

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

