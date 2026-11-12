"""Contract checks for structured chat output from an OpenAI-compatible gateway."""

from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app.services.chat_ai import ask_schedule_ai


def completion(content: dict, finish_reason: str = "stop") -> BytesIO:
    return BytesIO(json.dumps({
        "choices": [{
            "finish_reason": finish_reason,
            "message": {"content": json.dumps(content)},
        }],
    }).encode())


class ChatAIContractTests(TestCase):
    def setUp(self) -> None:
        self.env = patch.dict("os.environ", {
            "AI_BASE_URL": "http://127.0.0.1:4000/v1",
            "AI_API_KEY": "test-gateway-key",
            "AI_MODEL": "secretary-schedule",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_request_uses_strict_schema_and_maps_create_fields(self) -> None:
        model_output = {
            "intent": "create", "answer": "Please confirm", "schedule_ids": [],
            "schedule_id": None,
            "schedule_fields": [
                {"field": "title", "value": "Team meeting"},
                {"field": "starts_at_utc", "value": "2026-11-20T09:00:00+07:00"},
                {"field": "meeting_mode", "value": "online"},
            ],
        }
        with patch("app.services.chat_ai.urlopen", return_value=completion(model_output)) as send:
            result = ask_schedule_ai("Create a meeting", [], [], "2026-11-12T10:00:00+07:00")

        request = send.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(request.full_url, "http://127.0.0.1:4000/v1/chat/completions")
        self.assertEqual(body["model"], "secretary-schedule")
        self.assertEqual(body["response_format"]["type"], "json_schema")
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertFalse(body["response_format"]["json_schema"]["schema"]["additionalProperties"])
        self.assertEqual(result["schedule"], {
            "title": "Team meeting",
            "starts_at_utc": "2026-11-20T09:00:00+07:00",
            "meeting_mode": "online",
        })

    def test_null_field_preserves_clear_semantics_for_update(self) -> None:
        model_output = {
            "intent": "update", "answer": "Please confirm", "schedule_ids": [],
            "schedule_id": "de66add8-edb2-492a-89c4-c766956f8c01",
            "schedule_fields": [{"field": "location", "value": None}],
        }
        with patch("app.services.chat_ai.urlopen", return_value=completion(model_output)):
            result = ask_schedule_ai("Remove the location", [], [], "2026-11-12T10:00:00+07:00")
        self.assertEqual(result["schedule"], {"location": None})

    def test_rejects_duplicate_fields_and_truncated_response(self) -> None:
        model_output = {
            "intent": "create", "answer": "Please confirm", "schedule_ids": [],
            "schedule_id": None,
            "schedule_fields": [
                {"field": "title", "value": "One"},
                {"field": "title", "value": "Two"},
            ],
        }
        for response in (completion(model_output), completion(model_output, "length")):
            with self.subTest(response=response.getvalue()), patch(
                "app.services.chat_ai.urlopen", return_value=response
            ):
                with self.assertRaises(HTTPException) as error:
                    ask_schedule_ai("Create", [], [], "2026-11-12T10:00:00+07:00")
                self.assertEqual(error.exception.status_code, 502)
