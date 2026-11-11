"""Persisted chat context, pagination, and ownership tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

from app.api.chat import chat_message, confirm_proposal, list_chat_messages
from app.models.chat_message import ChatMessage
from app.schemas.chat import ChatMessageRequest
from tests._db_case import DatabaseCase


class ChatHistoryTests(DatabaseCase):
    def test_exchange_is_saved_and_reused_as_ai_context(self) -> None:
        answer = {"intent": "answer", "answer": "What time?"}
        with patch("app.api.chat.ask_schedule_ai", return_value=answer) as ask:
            chat_message(ChatMessageRequest(message="Plan a meeting"), self.user, self.db)
            chat_message(ChatMessageRequest(message="At nine"), self.user, self.db)
        self.assertEqual(
            ask.call_args.args[1],
            [
                {"role": "user", "content": "Plan a meeting"},
                {"role": "assistant", "content": "What time?"},
            ],
        )
        saved = self.db.scalars(
            select(ChatMessage).where(ChatMessage.user_id == self.user.id)
        ).all()
        self.assertEqual(len(saved), 4)

    def test_cursor_pages_and_ownership(self) -> None:
        start = datetime.now(timezone.utc)
        for index in range(5):
            self.db.add(ChatMessage(
                id=uuid4(), user_id=self.user.id, role="user",
                content=f"Message {index}",
                created_at_utc=start + timedelta(seconds=index),
            ))
        self.db.commit()
        first = list_chat_messages(limit=2, cursor=None, user=self.user, db=self.db)
        second = list_chat_messages(
            limit=2, cursor=first.next_cursor, user=self.user, db=self.db,
        )
        third = list_chat_messages(
            limit=2, cursor=second.next_cursor, user=self.user, db=self.db,
        )
        self.assertEqual([item.content for item in first.messages], ["Message 4", "Message 3"])
        self.assertEqual([item.content for item in second.messages], ["Message 2", "Message 1"])
        self.assertEqual([item.content for item in third.messages], ["Message 0"])
        self.assertIsNone(third.next_cursor)
        self.assertEqual(
            list_chat_messages(limit=20, cursor=None, user=self.other_user, db=self.db).messages,
            [],
        )
        with self.assertRaises(HTTPException) as error:
            list_chat_messages(
                limit=2, cursor=first.next_cursor, user=self.other_user, db=self.db,
            )
        self.assertEqual(error.exception.status_code, 400)

    def test_failed_ai_does_not_store_user_message(self) -> None:
        with patch(
            "app.api.chat.ask_schedule_ai",
            side_effect=HTTPException(status_code=502, detail="AI failed"),
        ):
            with self.assertRaises(HTTPException):
                chat_message(ChatMessageRequest(message="Plan a meeting"), self.user, self.db)
        saved = self.db.scalars(
            select(ChatMessage).where(ChatMessage.user_id == self.user.id)
        ).all()
        self.assertEqual(saved, [])

    def test_proposal_state_is_restored_after_confirmation(self) -> None:
        result = {
            "intent": "create", "answer": "Confirm this meeting",
            "schedule": {
                "title": "History meeting",
                "starts_at_utc": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                "meeting_mode": "online",
            },
        }
        with patch("app.api.chat.ask_schedule_ai", return_value=result):
            response = chat_message(
                ChatMessageRequest(message="Create meeting"), self.user, self.db,
            )
        page = list_chat_messages(limit=20, cursor=None, user=self.user, db=self.db)
        self.assertEqual(page.messages[0].proposal_state, "pending")
        self.assertEqual(page.messages[0].proposal.id, response.proposal.id)
        confirm_proposal(response.proposal.id, self.user, self.db)
        page = list_chat_messages(limit=20, cursor=None, user=self.user, db=self.db)
        self.assertEqual(page.messages[0].proposal_state, "confirmed")
