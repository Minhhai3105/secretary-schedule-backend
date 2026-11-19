"""Shared PostgreSQL fixture and clock for reminder tests."""

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.models.reminder_setting import ReminderSetting
from tests._db_case import DatabaseCase

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def relative_morning_utc() -> datetime:
    target_date = datetime.now(VIETNAM_TZ).date() + timedelta(days=1)
    return datetime.combine(
        target_date, time(8, 0), tzinfo=VIETNAM_TZ
    ).astimezone(timezone.utc)


class ReminderDatabaseCase(DatabaseCase):
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
