"""Authenticated completions and SSE for the schedule assistant."""
from datetime import datetime, timezone
import json
import logging
from os import environ
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from app.api.chat import chat_message, stream_chat_message
from app.core.auth import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.chat import ChatMessageRequest
from app.schemas.chat_completion import (
    ASSISTANT_MODEL, COMPLETION_RESPONSE_FORMAT, ChatCompletionRequest,
    ChatCompletionResponse, CompletionAssistantMessage, CompletionChoice, CompletionContent,
)

logger = logging.getLogger(__name__)


class CompletionStreamResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # A send-side disconnect must also close the upstream iterator.
            await self.body_iterator.aclose()


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


@router.post("/chat/completions", response_model=ChatCompletionResponse, responses={
    200: {"content": {"text/event-stream": {"schema": {"type": "string"}}}},
})
def create_chat_completion(
    payload: ChatCompletionRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.model != ASSISTANT_MODEL:
        raise HTTPException(status_code=404, detail="Schedule assistant model not found")
    if payload.response_format is not None and (
        payload.response_format.json_schema.schema_
        != COMPLETION_RESPONSE_FORMAT["json_schema"]["schema"]
    ):
        raise HTTPException(status_code=400, detail="Only the schedule assistant response schema is supported")
    if payload.stream:
        if not environ.get("AI_API_KEY"):
            raise HTTPException(status_code=503, detail="Chat AI is not configured")
        return _stream_completion(payload, request, user, db)
    response = chat_message(ChatMessageRequest(message=payload.messages[0].content), user, db)
    content = CompletionContent.from_chat(response).model_dump_json()
    return ChatCompletionResponse(
        id="chatcmpl-" + uuid4().hex, created=int(datetime.now(timezone.utc).timestamp()),
        model=ASSISTANT_MODEL,
        choices=[CompletionChoice(message=CompletionAssistantMessage(content=content))],
    )


def _stream_completion(payload, request: Request, user: User, db: Session) -> StreamingResponse:
    completion_id = "chatcmpl-" + uuid4().hex
    created = int(datetime.now(timezone.utc).timestamp())

    def chunk(delta, finish_reason=None):
        return "data: " + json.dumps({
            "id": completion_id, "object": "chat.completion.chunk", "created": created,
            "model": ASSISTANT_MODEL, "choices": [{
                "index": 0, "delta": delta, "finish_reason": finish_reason,
            }],
        }, ensure_ascii=False) + "\n\n"

    async def events():
        source = stream_chat_message(ChatMessageRequest(message=payload.messages[0].content), user, db)
        try:
            yield chunk({"role": "assistant", "content": '{"answer":"'})
            async for kind, value in iterate_in_threadpool(source):
                if await request.is_disconnected():
                    return
                if kind == "answer":
                    yield chunk({"content": json.dumps(value, ensure_ascii=False)[1:-1]})
                else:
                    content = CompletionContent.from_chat(value).model_dump(mode="json")
                    tail = json.dumps({"schedules": content["schedules"], "proposal": content["proposal"]},
                                      ensure_ascii=False, separators=(",", ":"))
                    yield chunk({"content": '\",' + tail[1:]})
                    yield chunk({}, "stop")
                    yield "data: [DONE]\n\n"
                    return
            raise ValueError("Missing completion result")
        except Exception as exc:
            logger.warning("Schedule completion stream failed: %s", type(exc).__name__)
            unavailable = isinstance(exc, HTTPException) and exc.status_code == 503
            yield "data: " + json.dumps({"error": {
                "message": "Chat AI is temporarily unavailable; reload history before retrying" if unavailable
                else "Schedule assistant stream failed; reload history before retrying",
                "type": "server_error", "param": None,
                "code": "ai_unavailable" if unavailable else "stream_failed",
            }}) + "\n\n"
        finally:
            await run_in_threadpool(source.close)

    return CompletionStreamResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no",
    })
