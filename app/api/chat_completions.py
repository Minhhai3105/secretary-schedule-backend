"""Authenticated, non-streaming completions for the schedule assistant."""
from datetime import datetime, timezone
import logging
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from app.api.chat import chat_message
from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatMessageRequest
from app.schemas.chat_completion import (
    ASSISTANT_MODEL, COMPLETION_RESPONSE_FORMAT, ChatCompletionRequest,
    ChatCompletionResponse, CompletionAssistantMessage, CompletionChoice, CompletionContent,
)

logger = logging.getLogger(__name__)


class CompletionRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def handle(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse(status_code=400, content={"error": {
                    "message": "Unsupported or invalid schedule assistant completion request",
                    "type": "invalid_request_error", "param": None, "code": "invalid_request",
                }})
            except HTTPException as exc:
                return JSONResponse(status_code=exc.status_code, headers=exc.headers, content={"error": {
                    "message": str(exc.detail),
                    "type": "invalid_request_error" if exc.status_code < 500 else "server_error",
                    "param": None, "code": str(exc.status_code),
                }})
            except Exception as exc:
                logger.error("Schedule completion failed: %s", type(exc).__name__)
                return JSONResponse(status_code=500, content={"error": {
                    "message": "Schedule assistant request failed", "type": "server_error",
                    "param": None, "code": "internal_error",
                }})
        return handle


router = APIRouter(prefix="/v1", tags=["Chat Completions"], route_class=CompletionRoute)


@router.get("/models")
def list_models(user: User = Depends(get_current_user)) -> dict:
    return {"object": "list", "data": [{
        "id": ASSISTANT_MODEL, "object": "model", "created": 0,
        "owned_by": "secretary-schedule",
    }]}


@router.post("/chat/completions", response_model=ChatCompletionResponse)
def create_chat_completion(
    payload: ChatCompletionRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ChatCompletionResponse:
    if payload.model != ASSISTANT_MODEL:
        raise HTTPException(status_code=404, detail="Schedule assistant model not found")
    if payload.response_format is not None and (
        payload.response_format.json_schema.schema_
        != COMPLETION_RESPONSE_FORMAT["json_schema"]["schema"]
    ):
        raise HTTPException(status_code=400, detail="Only the schedule assistant response schema is supported")
    response = chat_message(ChatMessageRequest(message=payload.messages[0].content), user, db)
    content = CompletionContent.from_chat(response).model_dump_json()
    return ChatCompletionResponse(
        id="chatcmpl-" + uuid4().hex, created=int(datetime.now(timezone.utc).timestamp()),
        model=ASSISTANT_MODEL,
        choices=[CompletionChoice(message=CompletionAssistantMessage(content=content))],
    )
