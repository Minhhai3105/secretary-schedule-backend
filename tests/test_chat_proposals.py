"""Chat proposal confirmation and access tests."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.api.chat import chat_message, confirm_proposal
from app.models.schedule import Schedule
from app.schemas.chat import ChatMessageRequest
from tests._db_case import DatabaseCase


class ChatProposalTests(DatabaseCase):
    def _create_proposal(self):
        result = {
            "intent": "create",
            "answer": "Please confirm this meeting.",
            "schedule": {
                "title": "Chat meeting",
                "starts_at_utc": (
                    datetime.now(timezone.utc) + timedelta(days=3)
                ).isoformat(),
                "meeting_mode": "online",
            },
        }
        with patch("app.api.chat.ask_schedule_ai", return_value=result):
            return chat_message(
                ChatMessageRequest(message="Create a meeting"),
                self.user,
                self.db,
            ).proposal

    def test_proposal_does_not_create_schedule(self) -> None:
        proposal = self._create_proposal()
        self.assertIsNotNone(proposal)
        count = self.db.query(Schedule).filter(Schedule.user_id == self.user.id).count()
        self.assertEqual(count, 0)

    def test_other_user_cannot_confirm_proposal(self) -> None:
        proposal = self._create_proposal()
        with self.assertRaises(HTTPException) as result:
            confirm_proposal(proposal.id, self.other_user, self.db)
        self.assertEqual(result.exception.status_code, 404)

    def test_confirmed_proposal_cannot_be_replayed(self) -> None:
        proposal = self._create_proposal()
        confirmed = confirm_proposal(proposal.id, self.user, self.db)
        self.assertEqual(confirmed.schedule.title, "Chat meeting")
        with self.assertRaises(HTTPException) as result:
            confirm_proposal(proposal.id, self.user, self.db)
        self.assertEqual(result.exception.status_code, 409)
