from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch

from fastapi import HTTPException

from app.services.chat_ai import stream_schedule_ai
from app.services.chat_stream import answer_prefix


class ProviderStream(BytesIO):
    headers = {"Content-Type": "text/event-stream"}


def wire(pieces, finish="stop", done=True):
    events = [{"choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
              for piece in pieces]
    if finish:
        events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
    text = ": heartbeat\r\n\r\n" + "".join("data: " + json.dumps(event) + "\r\n\r\n" for event in events)
    return ProviderStream((text + ("data: [DONE]\r\n\r\n" if done else "")).encode())


class ChatStreamTests(TestCase):
    def setUp(self):
        env = patch.dict("os.environ", {"AI_API_KEY": "test-only"})
        env.start()
        self.addCleanup(env.stop)

    def test_real_upstream_stream_mode_and_unicode_escape_boundaries(self):
        output = {"intent": "answer", "answer": 'Xin chào "bạn"\n😀', "schedule_ids": [],
                  "schedule_id": None, "schedule_fields": []}
        content = json.dumps(output)
        response = wire(list(content))
        with patch("app.services.chat_ai.urlopen", return_value=response) as send:
            events = list(stream_schedule_ai("Hello", [], [], "2026-11-13T09:00:00+07:00"))
        self.assertTrue(json.loads(send.call_args.args[0].data)["stream"])
        self.assertEqual("".join(value for kind, value in events if kind == "answer"), output["answer"])
        self.assertEqual(events[-1][0], "result")
        self.assertGreater(len(events), 5)
        self.assertTrue(response.closed)

    def test_truncated_failed_or_invalid_stream_has_no_result(self):
        content = json.dumps({"intent": "answer", "answer": "Hello", "schedule_ids": [],
                              "schedule_id": None, "schedule_fields": []})
        for response in (wire([content], done=False), wire([content], finish="length"),
                         wire([content], finish=None), wire(['{"answer":"Hi"}'])):
            with self.subTest(), patch("app.services.chat_ai.urlopen", return_value=response):
                received = []
                with self.assertRaises(HTTPException) as error:
                    for event in stream_schedule_ai("Hello", [], [], "2026-11-13T09:00:00+07:00"):
                        received.append(event)
                self.assertEqual(error.exception.status_code, 502)
                self.assertFalse(any(kind == "result" for kind, _ in received))
                self.assertTrue(response.closed)

    def test_answer_extraction_ignores_nested_or_escaped_answer_keys(self):
        self.assertEqual(answer_prefix('{"nested":{"answer":"Wrong"},"answer":"Right'), "Right")
        self.assertEqual(answer_prefix('{"other":"answer: nope","answer":"Hi\\u00'), "Hi")
        self.assertEqual(answer_prefix('{"answer":"Hi\\'), "Hi")

    def test_closing_stream_early_closes_provider_connection_without_final_result(self):
        response = wire(['{"answer":"Hello', ' world","intent":"answer",'
                         '"schedule_ids":[],"schedule_id":null,"schedule_fields":[]}'])
        with patch("app.services.chat_ai.urlopen", return_value=response):
            source = stream_schedule_ai("Hello", [], [], "2026-11-13T09:00:00+07:00")
            self.assertEqual(next(source), ("answer", "Hello"))
            source.close()
        self.assertTrue(response.closed)
