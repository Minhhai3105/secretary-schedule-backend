"""Database-backed API flow tests; set DATABASE_URL and PGPASSFILE before running."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.api.chat import chat_message, confirm_proposal
from app.api.schedules import (
    cancel_schedule,
    create_schedule,
    get_schedule,
    schedule_history,
    update_schedule,
)
from app.db.session import engine as application_engine
from app.models.schedule import Schedule  # Registers FK metadata.
from app.models.user import User
from app.schemas.chat import ChatMessageRequest
from app.schemas.schedule import ScheduleCancel, ScheduleCreate, ScheduleUpdate


class BackendApiFlows(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = application_engine.connect()
        self.transaction = self.connection.begin()
        self.db = Session(
            bind=self.connection, join_transaction_mode="create_savepoint"
        )
        suffix = uuid4().hex
        self.user = User(
            id=uuid4(), name="Test user", email=f"user-{suffix}@example.invalid",
            password_hash="test-only",
        )
        self.other_user = User(
            id=uuid4(), name="Other user", email=f"other-{suffix}@example.invalid",
            password_hash="test-only",
        )
        self.db.add_all([self.user, self.other_user])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.transaction.rollback()
        self.connection.close()

    def test_schedule_ownership_version_and_history(self) -> None:
        starts = datetime.now(timezone.utc) + timedelta(days=2)
        schedule = create_schedule(
            ScheduleCreate(
                title="Review meeting", starts_at_utc=starts,
                meeting_mode="offline", related_person="Tôi",
            ),
            self.user,
            self.db,
        )
        with self.assertRaises(HTTPException) as other_result:
            get_schedule(schedule.id, self.other_user, self.db)
        self.assertEqual(other_result.exception.status_code, 404)

        changed = update_schedule(
            schedule.id,
            ScheduleUpdate(version=1, title="Updated meeting"),
            self.user,
            self.db,
        )
        self.assertEqual(changed.version, 2)
        with self.assertRaises(HTTPException) as stale_result:
            update_schedule(
                schedule.id,
                ScheduleUpdate(version=1, title="Stale change"),
                self.user,
                self.db,
            )
        self.assertEqual(stale_result.exception.status_code, 409)

        cancelled = cancel_schedule(
            schedule.id, ScheduleCancel(version=2), self.user, self.db
        )
        self.assertIsNotNone(cancelled.cancelled_at_utc)
        history = schedule_history(
            q=None, history_type="cancelled", page=1,
            user=self.user, db=self.db,
        )
        self.assertEqual([item.id for item in history.schedules], [schedule.id])
        self.assertEqual(history.schedules[0].status, "cancelled")

    def test_chat_proposal_waits_for_confirmation_and_is_user_scoped(self) -> None:
        starts = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
        model_result = {
            "intent": "create",
            "answer": "Please confirm this meeting.",
            "schedule": {
                "title": "Chat meeting",
                "starts_at_utc": starts,
                "meeting_mode": "online",
            },
        }
        with patch("app.api.chat.ask_schedule_ai", return_value=model_result):
            response = chat_message(
                ChatMessageRequest(message="Create a meeting"),
                self.user,
                self.db,
            )
        self.assertIsNotNone(response.proposal)
        self.assertEqual(
            self.db.query(Schedule).filter(Schedule.user_id == self.user.id).count(),
            0,
        )
        with self.assertRaises(HTTPException) as other_result:
            confirm_proposal(response.proposal.id, self.other_user, self.db)
        self.assertEqual(other_result.exception.status_code, 404)

        confirmed = confirm_proposal(response.proposal.id, self.user, self.db)
        self.assertEqual(confirmed.schedule.title, "Chat meeting")
        with self.assertRaises(HTTPException) as replay_result:
            confirm_proposal(response.proposal.id, self.user, self.db)
        self.assertEqual(replay_result.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
