from datetime import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.reminder_setting import ReminderSetting
from app.models.user import User
from app.schemas.reminder_setting import ReminderSettingsData

router = APIRouter(prefix="/api/reminder-settings", tags=["Reminder settings"])


def settings_response(settings: ReminderSetting) -> ReminderSettingsData:
    return ReminderSettingsData(
        daily_enabled=settings.daily_enabled,
        daily_time=settings.daily_time_vietnam,
        near_enabled=settings.near_enabled,
        lead_minutes=settings.lead_minutes,
    )


@router.get("", response_model=ReminderSettingsData)
def get_reminder_settings(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReminderSettingsData:
    settings = db.get(ReminderSetting, user.id)
    if settings is None:
        return ReminderSettingsData(
            daily_enabled=False,
            daily_time=time(7, 0),
            near_enabled=False,
            lead_minutes=30,
        )
    return settings_response(settings)


@router.put("", response_model=ReminderSettingsData)
def put_reminder_settings(
    payload: ReminderSettingsData,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ReminderSettingsData:
    settings = db.get(ReminderSetting, user.id)
    if settings is None:
        settings = ReminderSetting(user_id=user.id)
        db.add(settings)

    settings.daily_enabled = payload.daily_enabled
    settings.daily_time_vietnam = payload.daily_time
    settings.near_enabled = payload.near_enabled
    settings.lead_minutes = payload.lead_minutes
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(settings)
    return settings_response(settings)
