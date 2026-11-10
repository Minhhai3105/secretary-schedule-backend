"""Rollback-backed PostgreSQL fixture shared by API test modules."""

import unittest
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.session import engine
from app.models.user import User


class DatabaseCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = engine.connect()
        self.transaction = self.connection.begin()
        self.db = Session(
            bind=self.connection, join_transaction_mode="create_savepoint"
        )
        suffix = uuid4().hex
        self.user = User(
            id=uuid4(), name="Test user", email=f"user-{suffix}@example.invalid",
            password_hash="test-only",
        )
        self.other_user = User(
            id=uuid4(), name="Other user", email=f"other-{suffix}@example.invalid",
            password_hash="test-only",
        )
        self.db.add_all([self.user, self.other_user])
        self.db.flush()

    def tearDown(self) -> None:
        self.db.close()
        self.transaction.rollback()
        self.connection.close()
