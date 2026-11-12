"""Partial reminder-settings update tests."""

from datetime import time

from fastapi import HTTPException

from app.api.reminder_settings import (
    get_reminder_settings,
    patch_reminder_settings,
    put_reminder_settings,
)
from app.schemas.reminder_setting import ReminderSettingsData, ReminderSettingsPatch
from tests._db_case import DatabaseCase


class ReminderSettingsPatchTests(DatabaseCase):
    def test_patch_changes_only_sent_fields_and_put_still_works(self) -> None:
        updated = patch_reminder_settings(
            ReminderSettingsPatch(daily_enabled=True, lead_minutes=45),
            self.user, self.db,
        )
        self.assertTrue(updated.daily_enabled)
        self.assertEqual(updated.lead_minutes, 45)
        self.assertEqual(updated.daily_time, time(7, 0))
        self.assertFalse(updated.near_enabled)
        self.assertFalse(get_reminder_settings(self.other_user, self.db).daily_enabled)

        replaced = put_reminder_settings(
            ReminderSettingsData(
                daily_enabled=False, daily_time=time(8, 0),
                near_enabled=True, lead_minutes=30,
            ),
            self.user, self.db,
        )
        self.assertEqual(replaced.daily_time, time(8, 0))
        self.assertTrue(replaced.near_enabled)

    def test_empty_and_null_patch_are_rejected(self) -> None:
        for payload in (ReminderSettingsPatch(), ReminderSettingsPatch(near_enabled=None)):
            with self.assertRaises(HTTPException) as error:
                patch_reminder_settings(payload, self.user, self.db)
            self.assertEqual(error.exception.status_code, 422)
