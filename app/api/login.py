from datetime import datetime, timedelta, timezone
from os import environ

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.refresh_tokens import hash_refresh_token, new_refresh_token
from app.core.security import verify_password
from app.core.tokens import create_access_token
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserResponse

SESSION_TTL = timedelta(days=30)
router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    user = db.scalar(
        select(User).where(User.email == str(payload.email).lower())
    )
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    secret_key = environ.get("JWT_SECRET", "")
    if len(secret_key) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters")

    now = datetime.now(timezone.utc)
    expires_at = now + SESSION_TTL
    auth_session = AuthSession(
        user_id=user.id,
        created_at_utc=now,
        expires_at_utc=expires_at,
    )
    raw_refresh_token = new_refresh_token()

    try:
        db.add(auth_session)
        db.flush()
        session_id = auth_session.id
        db.add(
            RefreshToken(
                session_id=session_id,
                token_hash=hash_refresh_token(raw_refresh_token),
                created_at_utc=now,
                expires_at_utc=expires_at,
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise

    access_token = create_access_token(
        user_id=user.id,
        session_id=session_id,
        secret_key=secret_key,
    )
    response.set_cookie(
        key="refresh_token",
        value=raw_refresh_token,
        max_age=int(SESSION_TTL.total_seconds()),
        path="/api/auth",
        secure=environ.get("AUTH_COOKIE_SECURE", "1") != "0",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"

    return LoginResponse(
        access_token=access_token,
        user=UserResponse.model_validate(user),
    )
