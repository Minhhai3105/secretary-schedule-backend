"""PostgreSQL-backed reminder tests for edited and cancelled schedules."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api.schedules import cancel_schedule, create_schedule, update_schedule
from app.models.email_delivery import EmailDelivery
from app.schemas.schedule import ScheduleCancel, ScheduleCreate, ScheduleUpdate
from app.services.reminders import enqueue_due_reminders, process_due_deliveries
from tests._reminder_case import ReminderDatabaseCase, VIETNAM_TZ


class ReminderScheduleChangeTests(ReminderDatabaseCase):
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
