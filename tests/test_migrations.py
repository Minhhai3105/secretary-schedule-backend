"""Migration upgrade paths in disposable PostgreSQL schemas."""
from datetime import datetime, timezone
from os import environ
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.models.chat_message import ChatMessage
from app.models.user import User


class MigrationTests(TestCase):
    def setUp(self) -> None:
        self.admin = create_engine(environ["DATABASE_URL"])
        self.addCleanup(self.admin.dispose)
        self.schema = "test_migrations_" + uuid4().hex
        with self.admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.addCleanup(self._drop_schema)
        url = make_url(environ["DATABASE_URL"]).update_query_dict({
            "options": "-csearch_path=" + self.schema,
        })
        self.url = url.render_as_string(hide_password=False)
        self.db_engine = create_engine(url)
        self.addCleanup(self.db_engine.dispose)
        root = Path(__file__).resolve().parents[1]
        self.config = Config(str(root / "alembic.ini"))
        self.config.set_main_option("script_location", str(root / "migrations"))

    def _drop_schema(self) -> None:
        with self.admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))

    def _upgrade(self, revision: str = "head") -> None:
        with patch.dict(environ, {"DATABASE_URL": self.url}):
            command.upgrade(self.config, revision)

    def _assert_head(self) -> None:
        with self.db_engine.connect() as connection:
            self.assertEqual(connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one(), "003")
        inspector = inspect(self.db_engine)
        self.assertTrue(inspector.has_table("chat_messages"))
        default = next(column["default"] for column in inspector.get_columns("schedules")
                       if column["name"] == "related_person")
        self.assertIn("'Me'", default)

    def test_fresh_database_has_one_head_and_can_upgrade_twice_then_downgrade(self) -> None:
        self.assertEqual(ScriptDirectory.from_config(self.config).get_heads(), ["003"])
        self._upgrade()
        self._assert_head()
        self._upgrade()
        self._assert_head()
        with patch.dict(environ, {"DATABASE_URL": self.url}):
            command.check(self.config)
            command.downgrade(self.config, "base")
        self.assertFalse(inspect(self.db_engine).has_table("users"))
        self.assertFalse(inspect(self.db_engine).has_table("chat_messages"))

    def test_related_person_002_database_gets_chat_table(self) -> None:
        self._upgrade("002")
        self.assertFalse(inspect(self.db_engine).has_table("chat_messages"))
        self._upgrade()
        self._assert_head()

    def test_legacy_chat_002_preserves_messages_and_applies_me_default(self) -> None:
        self._upgrade("001")
        # Reproduce the schema and marker of the former 002 chat migration.
        ChatMessage.__table__.create(self.db_engine)
        user_id, message_id = uuid4(), uuid4()
        with self.db_engine.begin() as connection:
            connection.execute(User.__table__.insert().values(
                id=user_id, name="Migration test", email="migration@example.invalid",
                password_hash="test-only",
            ))
            connection.execute(ChatMessage.__table__.insert().values(
                id=message_id, user_id=user_id, role="user", content="Keep this message",
                created_at_utc=datetime.now(timezone.utc),
            ))
            connection.execute(text("UPDATE alembic_version SET version_num = '002'"))
        self._upgrade()
        self._assert_head()
        with self.db_engine.connect() as connection:
            self.assertEqual(connection.execute(
                text("SELECT content FROM chat_messages WHERE id = :id"),
                {"id": message_id},
            ).scalar_one(), "Keep this message")
