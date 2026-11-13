"""The supported OpenAI Chat Completions contract for the schedule assistant."""
from copy import deepcopy
from typing import Literal
from uuid import UUID
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.schemas.chat import ChatMessageResponse
from app.schemas.schedule import ScheduleCreate

ASSISTANT_MODEL = "secretary-schedule"


class CompletionUserMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user"]
    content: str = Field(min_length=1, max_length=4000)

    @field_validator("content")
    @classmethod
    def require_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Message cannot be blank")
        return value


class SchemaDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    strict: Literal[True] = True
    schema_: dict = Field(alias="schema")


class CompletionResponseFormat(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["json_schema"]
    json_schema: SchemaDefinition


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", json_schema_extra={"examples": [{
        "model": ASSISTANT_MODEL,
        "messages": [{"role": "user", "content": "Plan a reading session"}],
        "stream": False,
    }]})
    model: str = Field(min_length=1, max_length=255)
    # Account-scoped history is loaded by the backend, not supplied by a client.
    messages: list[CompletionUserMessage] = Field(min_length=1, max_length=1)
    stream: bool = False
    n: Literal[1] = 1
    response_format: CompletionResponseFormat | None = None


class ProposalField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: Literal["title", "starts_at_utc", "ends_at_utc", "meeting_mode", "location",
                   "meeting_url", "related_person", "preparation", "notes"]
    value: str | None


class CompletionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    action: Literal["create", "update", "cancel"]
    schedule_id: UUID | None
    schedule_version: int | None
    proposed_data: list[ProposalField]
    expires_at_utc: datetime


class CompletionSchedule(ScheduleCreate):
    id: UUID
    cancelled_at_utc: AwareDatetime | None
    version: int
    status: Literal["upcoming", "past", "cancelled"]


class CompletionContent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer: str
    schedules: list[CompletionSchedule]
    proposal: CompletionProposal | None

    @classmethod
    def from_chat(cls, response: ChatMessageResponse) -> "CompletionContent":
        proposal = None
        if response.proposal is not None:
            proposal = response.proposal.model_dump(mode="json")
            proposal["proposed_data"] = [
                {"field": field, "value": value}
                for field, value in proposal["proposed_data"].items()
            ]
        return cls(answer=response.answer,
                   schedules=[schedule.model_dump(mode="json") for schedule in response.schedules],
                   proposal=proposal)


def _strict_schema(schema: dict) -> dict:
    result = deepcopy(schema)
    def visit(node: dict) -> None:
        node.pop("default", None)
        node.pop("readOnly", None)
        node.pop("format", None)
        if "properties" in node:
            node["additionalProperties"] = False
            node["required"] = list(node["properties"])
            for child in node["properties"].values():
                visit(child)
        for child in node.get("$defs", {}).values():
            visit(child)
        if isinstance(node.get("items"), dict):
            visit(node["items"])
        for keyword in ("anyOf", "oneOf", "allOf"):
            for child in node.get(keyword, []):
                visit(child)
    visit(result)
    return result


COMPLETION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "schedule_assistant_response",
        "strict": True,
        "schema": _strict_schema(CompletionContent.model_json_schema(mode="serialization")),
    },
}


class CompletionAssistantMessage(BaseModel):
    role: Literal["assistant"] = "assistant"
    content: str
    refusal: None = None


class CompletionChoice(BaseModel):
    index: Literal[0] = 0
    message: CompletionAssistantMessage
    finish_reason: Literal["stop"] = "stop"
    logprobs: None = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: Literal["chat.completion"] = "chat.completion"
    created: int
    model: str
    choices: list[CompletionChoice]
