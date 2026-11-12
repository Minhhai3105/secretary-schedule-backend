from datetime import datetime, timezone
from typing import Literal, Self
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    computed_field,
    field_validator,
    model_validator,
)


class ScheduleCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=255)
    starts_at_utc: AwareDatetime
    ends_at_utc: AwareDatetime | None = None
    meeting_mode: Literal["online", "offline"]
    location: str | None = Field(default=None, max_length=500)
    meeting_url: HttpUrl | None = Field(default=None, max_length=2048)
    related_person: str = Field(default="Me", max_length=255)
    preparation: str | None = None
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title is required")
        return value

    @field_validator("related_person", mode="before")
    @classmethod
    def clean_related_person(cls, value: object) -> object:
        if value is None:
            return "Me"
        if isinstance(value, str):
            person = value.strip()
            return "Me" if person in {"", "Tôi"} else person
        return value

    @field_validator("starts_at_utc", "ends_at_utc")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="after")
    def check_end_time(self) -> Self:
        if self.ends_at_utc is not None and self.ends_at_utc <= self.starts_at_utc:
            raise ValueError("End time must be after start time")
        return self


class ScheduleResponse(ScheduleCreate):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cancelled_at_utc: AwareDatetime | None = None
    version: int

    @computed_field
    @property
    def status(self) -> Literal["upcoming", "past", "cancelled"]:
        if self.cancelled_at_utc is not None:
            return "cancelled"
        if self.starts_at_utc < datetime.now(timezone.utc):
            return "past"
        return "upcoming"

    @field_validator("cancelled_at_utc")
    @classmethod
    def normalize_cancelled_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value is not None else None


class ScheduleUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=255)
    starts_at_utc: AwareDatetime | None = None
    ends_at_utc: AwareDatetime | None = None
    meeting_mode: Literal["online", "offline"] | None = None
    location: str | None = Field(default=None, max_length=500)
    meeting_url: HttpUrl | None = Field(default=None, max_length=2048)
    related_person: str | None = Field(default=None, max_length=255)
    preparation: str | None = None
    notes: str | None = None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("Title is required")
        return value

    @field_validator("related_person", mode="before")
    @classmethod
    def clean_related_person(cls, value: object) -> object:
        if value is None:
            return "Me"
        if isinstance(value, str):
            person = value.strip()
            return "Me" if person in {"", "Tôi"} else person
        return value

    @field_validator("starts_at_utc", "ends_at_utc")
    @classmethod
    def normalize_utc(cls, value: datetime | None) -> datetime | None:
        return value.astimezone(timezone.utc) if value is not None else None

    @model_validator(mode="after")
    def check_changes(self) -> Self:
        changed = self.model_fields_set - {"version"}
        if not changed:
            raise ValueError("At least one schedule field must be changed")
        for field in ("title", "starts_at_utc", "meeting_mode"):
            if field in changed and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class ScheduleCancel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1)


class ScheduleListResponse(BaseModel):
    schedules: list[ScheduleResponse]
    related_people: list[str]


class PageInfo(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class ScheduleHistoryResponse(BaseModel):
    schedules: list[ScheduleResponse]
    paging: PageInfo


class ScheduleConflictResponse(BaseModel):
    has_conflict: bool
    conflicts: list[ScheduleResponse]

