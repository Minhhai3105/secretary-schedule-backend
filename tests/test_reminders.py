"""PostgreSQL-backed email reminder worker and retry tests."""

from datetime import datetime, time, timedelta, timezone
from os import environ
import threading
from unittest.mock import MagicMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.api.email_deliveries import get_email_delivery, list_email_deliveries
from app.api.schedules import cancel_schedule, create_schedule, update_schedule
from app.db.base import Base
from app.db.session import engine
from app.models.email_attempt import EmailAttempt
from app.models.email_delivery import EmailDelivery
from app.models.reminder_setting import ReminderSetting
from app.models.schedule import Schedule
from app.models.user import User
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

        def fake_send(
            *, to_email: str, subject: str, body: str, html_body: str | None = None
        ) -> None:
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

        def failing_send(
            *, to_email: str, subject: str, body: str, html_body: str | None = None
        ) -> None:
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

        def fail_once(
            *, to_email: str, subject: str, body: str, html_body: str | None = None
        ) -> None:
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

        def record_send(
            *, to_email: str, subject: str, body: str, html_body: str | None = None
        ) -> None:
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

    def test_deliveries_send_both_plain_text_and_html_body(self) -> None:
        now = self._relative_morning_utc()
        schedule = create_schedule(
            ScheduleCreate(
                title="Sprint Planning & Review",
                starts_at_utc=now + timedelta(minutes=20),
                meeting_mode="online",
                meeting_url="https://meet.google.com/abc-xyz",
                preparation="Review backlog tickets",
                notes="Bring story point estimates",
            ),
            self.user,
            self.db,
        )

        sent_deliveries: list[dict[str, str]] = []

        def capture_send(*, to_email: str, subject: str, body: str, html_body: str | None = None) -> None:
            sent_deliveries.append({
                "to": to_email,
                "subject": subject,
                "body": body,
                "html": html_body or "",
            })

        enqueue_due_reminders(self.db, now_utc=now)
        processed = process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=capture_send,
        )

        near_item = next(item for item in sent_deliveries if "Upcoming" in item["subject"])
        self.assertIn("Sprint Planning & Review", near_item["subject"])
        # Plain text assertions
        self.assertIn("https://meet.google.com/abc-xyz", near_item["body"])
        self.assertIn("Review backlog tickets", near_item["body"])
        # HTML assertions
        self.assertIn("Tham gia cuộc họp", near_item["html"])
        self.assertIn("https://meet.google.com/abc-xyz", near_item["html"])
        self.assertIn("Review backlog tickets", near_item["html"])
        self.assertIn("Bring story point estimates", near_item["html"])

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

    def test_send_fn_type_error_fails_attempt_without_duplicate_call(self) -> None:
        now = self._relative_morning_utc()
        self.settings.near_enabled = False
        self.db.flush()
        enqueue_due_reminders(self.db, now_utc=now)

        call_count = 0

        def buggy_send(
            *, to_email: str, subject: str, body: str, html_body: str | None = None
        ) -> None:
            nonlocal call_count
            call_count += 1
            raise TypeError("internal sender error after dispatch")

        processed = process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=buggy_send,
        )
        self.assertEqual(len(processed), 1)
        self.assertEqual(call_count, 1)
        self.assertEqual(processed[0].status, "failed")

        attempt = self.db.scalar(
            select(EmailAttempt).where(EmailAttempt.delivery_id == processed[0].id)
        )
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.status, "failed")
        self.assertIn("TypeError", attempt.error_message or "")

    def test_concurrent_workers_enqueue_and_process_without_conflict_or_duplicate(
        self,
    ) -> None:
        schema = "test_reminders_conc_" + uuid4().hex
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))

        schema_url = make_url(environ["DATABASE_URL"]).update_query_dict({
            "options": "-csearch_path=" + schema,
        })
        schema_engine = create_engine(schema_url)
        try:
            Base.metadata.create_all(schema_engine)
            now = self._relative_morning_utc()
            user_id = uuid4()

            with Session(schema_engine) as seed_db:
                seed_db.add(
                    User(
                        id=user_id,
                        name="Concurrent Worker User",
                        email=f"conc-{uuid4().hex}@example.invalid",
                        password_hash="test-only",
                    )
                )
                seed_db.flush()
                seed_db.add(
                    ReminderSetting(
                        user_id=user_id,
                        daily_enabled=True,
                        daily_time_vietnam=time(7, 0),
                        near_enabled=True,
                        lead_minutes=30,
                    )
                )
                seed_db.add(
                    Schedule(
                        user_id=user_id,
                        title="Concurrent Standup",
                        starts_at_utc=now + timedelta(minutes=15),
                        meeting_mode="online",
                        meeting_url="https://meet.example.com/conc",
                        related_person="Me",
                    )
                )
                seed_db.commit()

            # 1) Two workers enqueue concurrently: neither raises IntegrityError
            barrier = threading.Barrier(2)
            enqueue_counts: list[int] = []
            enqueue_errors: list[Exception] = []
            lock = threading.Lock()

            def worker_enqueue() -> None:
                try:
                    with Session(schema_engine) as worker_db:
                        barrier.wait(timeout=5)
                        created = enqueue_due_reminders(worker_db, now_utc=now)
                        worker_db.commit()
                        with lock:
                            enqueue_counts.append(len(created))
                except Exception as exc:
                    with lock:
                        enqueue_errors.append(exc)

            t1 = threading.Thread(target=worker_enqueue)
            t2 = threading.Thread(target=worker_enqueue)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            self.assertEqual(enqueue_errors, [])
            self.assertEqual(sum(enqueue_counts), 2)

            with Session(schema_engine) as check_db:
                total_rows = len(check_db.scalars(select(EmailDelivery)).all())
                self.assertEqual(total_rows, 2)

            # 2) Two workers process concurrently with SKIP LOCKED: zero duplicates
            worker1_locked = threading.Event()
            release_worker1 = threading.Event()
            sent_subjects: list[str] = []
            process_errors: list[Exception] = []

            def slow_send(
                *, to_email: str, subject: str, body: str, html_body: str | None = None
            ) -> None:
                worker1_locked.set()
                release_worker1.wait(timeout=5)
                with lock:
                    sent_subjects.append(subject)

            def fast_send(
                *, to_email: str, subject: str, body: str, html_body: str | None = None
            ) -> None:
                with lock:
                    sent_subjects.append(subject)

            def run_worker1() -> None:
                try:
                    with Session(schema_engine) as w1_db:
                        process_due_deliveries(
                            w1_db,
                            now_utc=now,
                            retry_delay=timedelta(0),
                            send_fn=slow_send,
                        )
                        w1_db.commit()
                except Exception as exc:
                    with lock:
                        process_errors.append(exc)

            p1 = threading.Thread(target=run_worker1)
            p1.start()
            self.assertTrue(worker1_locked.wait(timeout=5))

            try:
                with Session(schema_engine) as w2_db:
                    w2_processed = process_due_deliveries(
                        w2_db,
                        now_utc=now,
                        retry_delay=timedelta(0),
                        send_fn=fast_send,
                    )
                    w2_db.commit()
                # Worker 1 already locked all due deliveries; Worker 2 skips without blocking
                self.assertEqual(len(w2_processed), 0)
            finally:
                release_worker1.set()
                p1.join(timeout=10)

            self.assertEqual(process_errors, [])
            self.assertEqual(len(sent_subjects), 2)
        finally:
            schema_engine.dispose()
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))

