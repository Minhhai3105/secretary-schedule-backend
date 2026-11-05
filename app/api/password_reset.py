from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.password_reset_tokens import (
    hash_password_reset_token,
    new_password_reset_token,
)
from app.core.security import hash_password
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.password_reset import PasswordReset
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.password_reset import (
    ForgotPasswordRequest,
    GenericMessage,
    ResetPasswordRequest,
)
from app.services.mailer import (
    password_reset_mail_configured,
    send_password_reset_email,
)

router = APIRouter(prefix="/api/auth", tags=["Auth"])
RESET_TTL = timedelta(minutes=30)
GENERIC_MESSAGE = "If this email is registered, a reset link will be sent."


@router.post(
    "/forgot-password",
    response_model=GenericMessage,
    status_code=status.HTTP_202_ACCEPTED,
)
def forgot_password(
    payload: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> GenericMessage:
    if not password_reset_mail_configured():
        raise HTTPException(
            status_code=503, detail="Password reset email is unavailable"
        )

    raw_token = new_password_reset_token()
    token_hash = hash_password_reset_token(raw_token)
    user = db.scalar(
        select(User).where(User.email == str(payload.email).lower())
    )
    if user is not None:
        db.add(
            PasswordReset(
                user_id=user.id,
                token_hash=token_hash,
                expires_at_utc=datetime.now(timezone.utc) + RESET_TTL,
            )
        )
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise
        background_tasks.add_task(
            send_password_reset_email, user.email, raw_token
        )
    return GenericMessage(message=GENERIC_MESSAGE)


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
def reset_password(
    payload: ResetPasswordRequest,
    db: Session = Depends(get_db),
) -> None:
    now = datetime.now(timezone.utc)
    reset = db.scalar(
        select(PasswordReset)
        .where(
            PasswordReset.token_hash == hash_password_reset_token(payload.token)
        )
        .with_for_update()
    )
    if reset is None or reset.used_at_utc is not None or reset.expires_at_utc <= now:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user = db.scalar(
        select(User).where(User.id == reset.user_id).with_for_update()
    )
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")

    user.password_hash = hash_password(payload.password)
    db.execute(
        update(PasswordReset)
        .where(
            PasswordReset.user_id == user.id,
            PasswordReset.used_at_utc.is_(None),
        )
        .values(used_at_utc=now)
    )
    db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == user.id,
            AuthSession.revoked_at_utc.is_(None),
        )
        .values(revoked_at_utc=now)
    )
    session_ids = select(AuthSession.id).where(AuthSession.user_id == user.id)
    db.execute(
        update(RefreshToken)
        .where(
            RefreshToken.session_id.in_(session_ids),
            RefreshToken.revoked_at_utc.is_(None),
        )
        .values(revoked_at_utc=now)
    )
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise
