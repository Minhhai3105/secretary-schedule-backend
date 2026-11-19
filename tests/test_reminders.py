"""PostgreSQL-backed tests for sending and retrying email reminders."""

from datetime import timedelta

from sqlalchemy import select

from app.api.email_deliveries import get_email_delivery, list_email_deliveries
from app.api.schedules import create_schedule
from app.models.email_attempt import EmailAttempt
from app.models.email_delivery import EmailDelivery
from app.schemas.schedule import ScheduleCreate
from app.services.reminders import (
    MAX_ATTEMPTS,
    enqueue_due_reminders,
    process_due_deliveries,
)
from tests._reminder_case import (
    ReminderDatabaseCase,
    VIETNAM_TZ,
    relative_morning_utc,
)


class ReminderWorkerTests(ReminderDatabaseCase):
    def test_daily_and_near_reminders_enqueued_and_sent_once(self) -> None:
        now = relative_morning_utc()
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
        now = relative_morning_utc()
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

    def test_deliveries_send_both_plain_text_and_html_body(self) -> None:
        now = relative_morning_utc()
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

    def test_send_fn_type_error_fails_attempt_without_duplicate_call(self) -> None:
        now = relative_morning_utc()
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

    def test_batch_size_limits_cycle_and_skips_exhausted_failed_deliveries(self) -> None:
        now = relative_morning_utc()
        # Create an earlier failed delivery that already reached MAX_ATTEMPTS
        exhausted = EmailDelivery(
            user_id=self.user.id,
            schedule_id=None,
            event_key=f"daily:{self.user.id}:exhausted",
            kind="daily",
            applicable_date_vietnam=now.astimezone(VIETNAM_TZ).date(),
            due_at_utc=now - timedelta(hours=2),
            status="failed",
        )
        self.db.add(exhausted)
        self.db.flush()
        for num in range(1, MAX_ATTEMPTS + 1):
            self.db.add(
                EmailAttempt(
                    delivery_id=exhausted.id,
                    attempt_number=num,
                    status="failed",
                    started_at_utc=now - timedelta(hours=1),
                    finished_at_utc=now - timedelta(hours=1),
                    error_message="ConnectionError: failed",
                )
            )

        create_schedule(
            ScheduleCreate(
                title="Batch meeting",
                starts_at_utc=now + timedelta(minutes=20),
                meeting_mode="online",
                meeting_url="https://meet.example.com/batch",
            ),
            self.user,
            self.db,
        )
        created = enqueue_due_reminders(self.db, now_utc=now)
        self.assertEqual(len(created), 2)

        sent_subjects: list[str] = []

        def record_send(
            *, to_email: str, subject: str, body: str, html_body: str | None = None
        ) -> None:
            sent_subjects.append(subject)

        # batch_size=1 must skip the exhausted delivery and send 1 fresh eligible email
        first_batch = process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=record_send,
            batch_size=1,
        )
        self.assertEqual(len(first_batch), 1)
        self.assertEqual(len(sent_subjects), 1)

        second_batch = process_due_deliveries(
            self.db,
            now_utc=now,
            retry_delay=timedelta(0),
            send_fn=record_send,
            batch_size=1,
        )
        self.assertEqual(len(second_batch), 1)
        self.assertEqual(len(sent_subjects), 2)
