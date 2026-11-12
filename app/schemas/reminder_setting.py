from datetime import time

from pydantic import BaseModel, Field, field_validator


class ReminderSettingsData(BaseModel):
    daily_enabled: bool
    daily_time: time
    near_enabled: bool
    lead_minutes: int = Field(ge=1, le=10080)

    @field_validator("daily_time")
    @classmethod
    def local_time_only(cls, value: time) -> time:
        if value.tzinfo is not None:
            raise ValueError("daily_time must be local Vietnam time without an offset")
        return value


class ReminderSettingsPatch(BaseModel):
    daily_enabled: bool | None = None
    daily_time: time | None = None
    near_enabled: bool | None = None
    lead_minutes: int | None = Field(default=None, ge=1, le=10080)

    @field_validator("daily_time")
    @classmethod
    def local_time_only(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("daily_time must be local Vietnam time without an offset")
        return value
