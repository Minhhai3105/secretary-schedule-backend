from collections.abc import Iterator
from os import environ

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

engine = create_engine(environ["DATABASE_URL"])


def get_db() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
