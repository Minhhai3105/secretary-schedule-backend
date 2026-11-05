from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt

ACCESS_TOKEN_TTL = timedelta(minutes=15)
JWT_ALGORITHM = "HS256"


def create_access_token(
    *, user_id: UUID, session_id: UUID, secret_key: str
) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "sid": str(session_id),
            "iat": now,
            "exp": now + ACCESS_TOKEN_TTL,
        },
        secret_key,
        algorithm=JWT_ALGORITHM,
    )
