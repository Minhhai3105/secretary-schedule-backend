from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.schedule import ScheduleResponse


class ChatTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class ChatMessageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=4000)
    history: list[ChatTurn] = Field(default_factory=list, max_length=5)


class ChatProposalResponse(BaseModel):
    id: UUID
    action: Literal["create", "update", "cancel"]
    schedule_id: UUID | None
    schedule_version: int | None
    proposed_data: dict
    expires_at_utc: datetime


class ChatMessageResponse(BaseModel):
    answer: str
    schedules: list[ScheduleResponse] = Field(default_factory=list)
    proposal: ChatProposalResponse | None = None


class ChatConfirmResponse(BaseModel):
    schedule: ScheduleResponse
