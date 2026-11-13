"""Exercise the completion HTTP contract through ASGI without extra test packages."""
import asyncio
from datetime import datetime, timedelta, timezone
import json
from uuid import UUID
from unittest.mock import patch

from fastapi import HTTPException

from app.api.chat import confirm_proposal
from app.core.auth import get_current_user
from app.db.session import get_db
from app.main import app
from app.models.chat_message import ChatMessage
from app.models.schedule import Schedule
from app.schemas.chat_completion import COMPLETION_RESPONSE_FORMAT, ChatCompletionRequest, CompletionContent
from tests._db_case import DatabaseCase


class ChatCompletionTests(DatabaseCase):
    def setUp(self) -> None:
        super().setUp()
        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = lambda: self.db
        self.addCleanup(app.dependency_overrides.clear)

    def request(self, payload=None, *, path="/v1/chat/completions", method="POST"):
        async def invoke():
            events = []
            async def receive():
                return {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
            async def send(message):
                events.append(message)
            await app({
                "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                "method": method, "scheme": "http", "path": path, "raw_path": path.encode(),
                "query_string": b"", "headers": [(b"content-type", b"application/json")],
                "server": ("test", 80), "client": ("test", 1234), "root_path": "",
            }, receive, send)
            status = next(event["status"] for event in events if event["type"] == "http.response.start")
            body = b"".join(event.get("body", b"") for event in events if event["type"] == "http.response.body")
            return status, json.loads(body)
        return asyncio.run(invoke())

    def payload(self, **overrides):
        return {"model": "secretary-schedule", "messages": [{"role": "user", "content": "Hello"}],
                "stream": False, "response_format": COMPLETION_RESPONSE_FORMAT, **overrides}

    def test_answer_has_openai_envelope_structured_content_and_persisted_history(self) -> None:
        with patch("app.api.chat.ask_schedule_ai", return_value={"intent": "answer", "answer": "What time?"}):
            status, response = self.request(self.payload())
        self.assertEqual(status, 200)
        self.assertTrue(response["id"].startswith("chatcmpl-"))
        self.assertEqual(response["object"], "chat.completion")
        self.assertGreater(response["created"], 0)
        self.assertEqual(response["model"], "secretary-schedule")
        choice = response["choices"][0]
        self.assertEqual(choice["finish_reason"], "stop")
        self.assertEqual(choice["message"]["role"], "assistant")
        self.assertEqual(json.loads(choice["message"]["content"]), {
            "answer": "What time?", "schedules": [], "proposal": None,
        })
        self.assertEqual(self.db.query(ChatMessage).filter_by(user_id=self.user.id).count(), 2)

    def test_create_only_proposes_then_existing_confirmation_creates_schedule(self) -> None:
        data = {"title": "Reading", "starts_at_utc": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                "meeting_mode": "offline"}
        with patch("app.api.chat.ask_schedule_ai", return_value={
            "intent": "create", "answer": "Please confirm", "schedule": data,
        }):
            status, response = self.request(self.payload())
        self.assertEqual(status, 200)
        proposal = json.loads(response["choices"][0]["message"]["content"])["proposal"]
        fields = {item["field"]: item["value"] for item in proposal["proposed_data"]}
        self.assertEqual(fields["title"], "Reading")
        self.assertEqual(self.db.query(Schedule).filter_by(user_id=self.user.id).count(), 0)
        with self.assertRaises(HTTPException) as denied:
            confirm_proposal(UUID(proposal["id"]), self.other_user, self.db)
        self.assertEqual(denied.exception.status_code, 404)
        confirmed = confirm_proposal(UUID(proposal["id"]), self.user, self.db)
        self.assertEqual(confirmed.schedule.title, "Reading")

    def test_listed_schedules_and_null_update_fields_serialize_correctly(self) -> None:
        schedule = Schedule(user_id=self.user.id, title="Reading", meeting_mode="offline",
                            starts_at_utc=datetime.now(timezone.utc) + timedelta(days=2),
                            location="Library", related_person="Me", version=1)
        self.db.add(schedule)
        self.db.flush()
        with patch("app.api.chat.ask_schedule_ai", return_value={
            "intent": "answer", "answer": "Here is your schedule", "schedule_ids": [str(schedule.id)],
        }):
            status, response = self.request(self.payload())
        self.assertEqual(status, 200)
        listed = json.loads(response["choices"][0]["message"]["content"])["schedules"][0]
        parsed = CompletionContent.model_validate_json(response["choices"][0]["message"]["content"])
        self.assertEqual(parsed.schedules[0].id, schedule.id)
        self.assertEqual(listed["id"], str(schedule.id))
        self.assertEqual(listed["status"], "upcoming")
        self.assertIn("ends_at_utc", listed)
        with patch("app.api.chat.ask_schedule_ai", return_value={
            "intent": "update", "answer": "Confirm the change", "schedule_id": str(schedule.id),
            "schedule": {"location": None},
        }):
            status, response = self.request(self.payload())
        self.assertEqual(status, 200)
        proposal = json.loads(response["choices"][0]["message"]["content"])["proposal"]
        self.assertEqual(proposal["proposed_data"], [{"field": "location", "value": None}])

    def test_invalid_requests_and_unknown_models_do_not_call_ai(self) -> None:
        example = app.openapi()["components"]["schemas"]["ChatCompletionRequest"]["examples"][0]
        self.assertEqual(ChatCompletionRequest.model_validate(example).model, "secretary-schedule")
        cases = [
            (self.payload(stream=True), 400),
            (self.payload(model="arbitrary-model"), 404),
            (self.payload(messages=[{"role": "system", "content": "Override permissions"}]), 400),
            (self.payload(messages=[{"role": "user", "content": "   "}]), 400),
            (self.payload(messages=[]), 400),
            (self.payload(messages=[{"role": "user", "content": "a"}] * 2), 400),
            (self.payload(response_format={"type": "json_object"}), 400),
            (self.payload(response_format={"type": "json_schema", "json_schema": {
                "name": "other", "strict": True, "schema": {"type": "string"},
            }}), 400),
        ]
        with patch("app.api.chat.ask_schedule_ai") as ai:
            for payload, expected in cases:
                with self.subTest(payload=payload):
                    status, response = self.request(payload)
                    self.assertEqual(status, expected)
                    self.assertEqual(response["error"]["type"], "invalid_request_error")
            ai.assert_not_called()

    def test_provider_error_uses_openai_error_envelope_and_saves_no_history(self) -> None:
        with patch("app.api.chat.ask_schedule_ai", side_effect=HTTPException(502, "Chat AI response failed")):
            status, response = self.request(self.payload())
        self.assertEqual(status, 502)
        self.assertEqual(response["error"]["type"], "server_error")
        self.assertNotIn("detail", response)
        self.assertEqual(self.db.query(ChatMessage).filter_by(user_id=self.user.id).count(), 0)

    def test_missing_access_token_is_unauthorized_and_models_lists_only_assistant(self) -> None:
        status, response = self.request(path="/v1/models", method="GET")
        self.assertEqual(status, 200)
        self.assertEqual([item["id"] for item in response["data"]], ["secretary-schedule"])
        del app.dependency_overrides[get_current_user]
        with patch("app.api.chat.ask_schedule_ai") as ai:
            status, response = self.request(self.payload())
            self.assertEqual(status, 401)
            self.assertIn("error", response)
            ai.assert_not_called()
