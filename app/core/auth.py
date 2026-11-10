from datetime import datetime, timezone
from os import environ
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.core.tokens import JWT_ALGORITHM
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.user import User

bearer = HTTPBearer(auto_error=False)


def unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired access token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise unauthorized()

    secret_key = environ.get("JWT_SECRET", "")
    if len(secret_key) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters")

    try:
        claims = jwt.decode(
            credentials.credentials,
            secret_key,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["sub", "sid", "iat", "exp"]},
        )
        sub = claims["sub"]
        sid = claims["sid"]
        if not isinstance(sub, str) or not isinstance(sid, str):
            raise ValueError("Invalid token claims")
        user_id = UUID(sub)
        session_id = UUID(sid)
    except (InvalidTokenError, ValueError, TypeError):
        raise unauthorized() from None

    auth_session = db.get(AuthSession, session_id)
    if (
        auth_session is None
        or auth_session.user_id != user_id
        or auth_session.revoked_at_utc is not None
        or auth_session.expires_at_utc <= datetime.now(timezone.utc)
    ):
        raise unauthorized()

    user = db.get(User, user_id)
    if user is None:
        raise unauthorized()
    return user
