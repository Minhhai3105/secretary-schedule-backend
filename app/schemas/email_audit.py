from datetime import date
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict


class EmailAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    attempt_number: int
    status: str
    started_at_utc: AwareDatetime
    finished_at_utc: AwareDatetime | None
    error_message: str | None


class EmailDeliveryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    schedule_id: UUID | None
    event_key: str
    kind: str
    applicable_date_vietnam: date | None
    due_at_utc: AwareDatetime
    status: str
    sent_at_utc: AwareDatetime | None


class EmailDeliveryDetail(EmailDeliveryResponse):
    attempts: list[EmailAttemptResponse]


class EmailPageInfo(BaseModel):
    page: int
    page_size: int
    total: int
    total_pages: int


class EmailDeliveryPage(BaseModel):
    deliveries: list[EmailDeliveryResponse]
    paging: EmailPageInfo
