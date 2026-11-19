"""Concurrent reminder cycles against an isolated PostgreSQL schema."""

import threading
from datetime import time, timedelta
from os import environ
from unittest import TestCase
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.session import engine
from app.models.email_delivery import EmailDelivery
from app.models.reminder_setting import ReminderSetting
from app.models.schedule import Schedule
from app.models.user import User
from app.services.reminders import enqueue_due_reminders, run_reminder_cycle
from tests._reminder_case import relative_morning_utc


class ReminderConcurrencyTests(TestCase):
    def test_concurrent_run_reminder_cycles_do_not_block_enqueue_or_hog_other_deliveries(
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
            now = relative_morning_utc()
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

            # Worker A runs full run_reminder_cycle(): enqueues BOTH due emails (daily + near),
            # commits enqueue, then pauses inside SMTP while sending the first email.
            # Worker B runs full run_reminder_cycle() concurrently while Worker A is still in SMTP:
            # - Worker B must NOT block at enqueue on Worker A's transactionid.
            # - Worker B must immediately claim and send the second independent email.
            worker1_in_smtp = threading.Event()
            release_worker1 = threading.Event()
            lock = threading.Lock()
            w1_sent: list[str] = []
            w2_sent: list[str] = []
            worker_errors: list[Exception] = []

            def slow_send(
                *, to_email: str, subject: str, body: str, html_body: str | None = None
            ) -> None:
                worker1_in_smtp.set()
                release_worker1.wait(timeout=5)
                with lock:
                    w1_sent.append(subject)

            def fast_send(
                *, to_email: str, subject: str, body: str, html_body: str | None = None
            ) -> None:
                with lock:
                    w2_sent.append(subject)

            def run_worker1() -> None:
                try:
                    with Session(schema_engine) as w1_db:
                        run_reminder_cycle(
                            w1_db,
                            now_utc=now,
                            retry_delay=timedelta(0),
                            send_fn=slow_send,
                        )
                except Exception as exc:
                    with lock:
                        worker_errors.append(exc)

            worker2_done = threading.Event()
            w2_processed_count: list[int] = []

            def run_worker2() -> None:
                try:
                    with Session(schema_engine) as w2_db:
                        processed = run_reminder_cycle(
                            w2_db,
                            now_utc=now,
                            retry_delay=timedelta(0),
                            send_fn=fast_send,
                        )
                        with lock:
                            w2_processed_count.append(len(processed))
                except Exception as exc:
                    with lock:
                        worker_errors.append(exc)
                finally:
                    worker2_done.set()

            p1 = threading.Thread(target=run_worker1)
            p1.start()
            self.assertTrue(worker1_in_smtp.wait(timeout=5))

            try:
                p2 = threading.Thread(target=run_worker2)
                p2.start()
                # Worker B must finish its entire cycle (enqueue + sending email #2)
                # BEFORE Worker A is released from SMTP!
                self.assertTrue(
                    worker2_done.wait(timeout=5),
                    "Worker B was blocked while Worker A was sending SMTP",
                )
                p2.join(timeout=5)
                self.assertEqual(w2_processed_count, [1])
                self.assertEqual(len(w2_sent), 1)
            finally:
                release_worker1.set()
                p1.join(timeout=10)

            self.assertEqual(worker_errors, [])
            self.assertEqual(len(w1_sent), 1)
            self.assertEqual(len(w2_sent), 1)
            self.assertEqual(len(set(w1_sent + w2_sent)), 2)

            with Session(schema_engine) as check_db:
                deliveries = check_db.scalars(select(EmailDelivery)).all()
                self.assertEqual(len(deliveries), 2)
                self.assertTrue(all(item.status == "sent" for item in deliveries))

            # Also verify two simultaneous enqueue calls on a brand-new schedule
            # never raise IntegrityError.
            with Session(schema_engine) as seed_db:
                seed_db.add(
                    Schedule(
                        user_id=user_id,
                        title="Simultaneous Enqueue Check",
                        starts_at_utc=now + timedelta(minutes=20),
                        meeting_mode="offline",
                        location="Room B",
                        related_person="Me",
                    )
                )
                seed_db.commit()

            barrier = threading.Barrier(2)
            enqueue_counts: list[int] = []
            enqueue_errors: list[Exception] = []

            def concurrent_enqueue() -> None:
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

            t1 = threading.Thread(target=concurrent_enqueue)
            t2 = threading.Thread(target=concurrent_enqueue)
            t1.start()
            t2.start()
            t1.join(timeout=10)
            t2.join(timeout=10)

            self.assertEqual(enqueue_errors, [])
            self.assertEqual(sum(enqueue_counts), 1)
        finally:
            schema_engine.dispose()
            with engine.begin() as conn:
                conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
