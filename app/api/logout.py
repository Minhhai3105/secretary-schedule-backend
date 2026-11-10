from datetime import datetime, timezone
from os import environ

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.refresh_tokens import hash_refresh_token
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.refresh_token import RefreshToken

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> None:
    response.delete_cookie(
        key="refresh_token",
        path="/api/auth",
        secure=environ.get("AUTH_COOKIE_SECURE", "1") != "0",
        httponly=True,
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"

    raw_token = request.cookies.get("refresh_token")
    if not raw_token:
        return

    session_id = db.scalar(
        select(RefreshToken.session_id).where(
            RefreshToken.token_hash == hash_refresh_token(raw_token)
        )
    )
    if session_id is None:
        return

    auth_session = db.scalar(
        select(AuthSession)
        .where(AuthSession.id == session_id)
        .with_for_update()
    )
    if auth_session is None:
        return

    now = datetime.now(timezone.utc)
    if auth_session.revoked_at_utc is None:
        auth_session.revoked_at_utc = now

    try:
        db.execute(
            update(RefreshToken)
            .where(
                RefreshToken.session_id == session_id,
                RefreshToken.revoked_at_utc.is_(None),
            )
            .values(revoked_at_utc=now)
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
