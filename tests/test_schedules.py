"""Schedule ownership, version, and history tests."""

from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from app.api.schedules import (
    cancel_schedule,
    create_schedule,
    get_schedule,
    schedule_history,
    update_schedule,
)
from app.schemas.schedule import ScheduleCancel, ScheduleCreate, ScheduleUpdate
from tests._db_case import DatabaseCase


class ScheduleTests(DatabaseCase):
    def _create_schedule(self):
        return create_schedule(
            ScheduleCreate(
                title="Review meeting",
                starts_at_utc=datetime.now(timezone.utc) + timedelta(days=2),
                meeting_mode="offline",
                related_person="Tôi",
            ),
            self.user,
            self.db,
        )

    def test_other_user_cannot_read_schedule(self) -> None:
        schedule = self._create_schedule()
        with self.assertRaises(HTTPException) as result:
            get_schedule(schedule.id, self.other_user, self.db)
        self.assertEqual(result.exception.status_code, 404)

    def test_stale_version_cannot_update_schedule(self) -> None:
        schedule = self._create_schedule()
        changed = update_schedule(
            schedule.id, ScheduleUpdate(version=1, title="Updated meeting"),
            self.user, self.db,
        )
        self.assertEqual(changed.version, 2)
        with self.assertRaises(HTTPException) as result:
            update_schedule(
                schedule.id, ScheduleUpdate(version=1, title="Stale change"),
                self.user, self.db,
            )
        self.assertEqual(result.exception.status_code, 409)

    def test_cancelled_schedule_appears_in_history(self) -> None:
        schedule = self._create_schedule()
        cancelled = cancel_schedule(
            schedule.id, ScheduleCancel(version=1), self.user, self.db
        )
        self.assertIsNotNone(cancelled.cancelled_at_utc)
        history = schedule_history(
            q=None, history_type="cancelled", page=1,
            user=self.user, db=self.db,
        )
        self.assertEqual([item.id for item in history.schedules], [schedule.id])
        self.assertEqual(history.schedules[0].status, "cancelled")
