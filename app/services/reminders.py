from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.email_attempt import EmailAttempt
from app.models.email_delivery import EmailDelivery
from app.models.reminder_setting import ReminderSetting
from app.models.schedule import Schedule
from app.models.user import User
from app.services.mailer import send_email_message

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MAX_ATTEMPTS = 4
DEFAULT_RETRY_DELAY = timedelta(minutes=5)
UPDATE_LOOKBACK = timedelta(hours=24)

SendEmailFn = Callable[..., None]


def _format_vn_datetime(value: datetime | None) -> str:
    if value is None:
        return "N/A"
    return value.astimezone(VIETNAM_TZ).strftime("%Y-%m-%d %H:%M (GMT+7)")


def _daily_event_key(user_id: object, vn_date: date) -> str:
    return f"daily:{user_id}:{vn_date.isoformat()}"


def _near_event_key(schedule: Schedule) -> str:
    return f"near:{schedule.id}:v{schedule.version}"


def _update_event_key(schedule: Schedule) -> str:
    return f"update:{schedule.id}:v{schedule.version}"


def cancel_stale_schedule_deliveries(db: Session) -> int:
    active_deliveries = db.scalars(
        select(EmailDelivery).where(
            EmailDelivery.kind.in_(("near", "update")),
            EmailDelivery.status.in_(("pending", "failed")),
        )
    ).all()
    cancelled_count = 0
    for delivery in active_deliveries:
        schedule = (
            db.get(Schedule, delivery.schedule_id, populate_existing=True)
            if delivery.schedule_id is not None
            else None
        )
        if delivery.kind == "near":
            is_stale = (
                schedule is None
                or schedule.cancelled_at_utc is not None
                or delivery.event_key != _near_event_key(schedule)
            )
        else:
            is_stale = (
                schedule is None
                or delivery.event_key != _update_event_key(schedule)
            )
        if is_stale:
            delivery.status = "cancelled"
            cancelled_count += 1
    if cancelled_count:
        db.flush()
    return cancelled_count


def enqueue_due_reminders(
    db: Session, *, now_utc: datetime | None = None
) -> list[EmailDelivery]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cancel_stale_schedule_deliveries(db)

    created: list[EmailDelivery] = []
    vn_now = now.astimezone(VIETNAM_TZ)
    vn_today = vn_now.date()

    settings_list = db.scalars(
        select(ReminderSetting).where(
            or_(
                ReminderSetting.daily_enabled.is_(True),
                ReminderSetting.near_enabled.is_(True),
            )
        )
    ).all()

    for setting in settings_list:
        if setting.daily_enabled:
            due_vn = datetime.combine(
                vn_today, setting.daily_time_vietnam, tzinfo=VIETNAM_TZ
            )
            due_utc = due_vn.astimezone(timezone.utc)
            if due_utc <= now:
                event_key = _daily_event_key(setting.user_id, vn_today)
                existing = db.scalar(
                    select(EmailDelivery).where(EmailDelivery.event_key == event_key)
                )
                if existing is None:
                    delivery = EmailDelivery(
                        user_id=setting.user_id,
                        schedule_id=None,
                        event_key=event_key,
                        kind="daily",
                        applicable_date_vietnam=vn_today,
                        due_at_utc=due_utc,
                        status="pending",
                    )
                    db.add(delivery)
                    created.append(delivery)

        if setting.near_enabled:
            horizon_utc = now + timedelta(minutes=setting.lead_minutes)
            upcoming_schedules = db.scalars(
                select(Schedule).where(
                    Schedule.user_id == setting.user_id,
                    Schedule.cancelled_at_utc.is_(None),
                    Schedule.starts_at_utc > now,
                    Schedule.starts_at_utc <= horizon_utc,
                )
            ).all()
            for schedule in upcoming_schedules:
                event_key = _near_event_key(schedule)
                existing = db.scalar(
                    select(EmailDelivery).where(EmailDelivery.event_key == event_key)
                )
                if existing is None:
                    due_utc = schedule.starts_at_utc - timedelta(
                        minutes=setting.lead_minutes
                    )
                    vn_date = schedule.starts_at_utc.astimezone(VIETNAM_TZ).date()
                    delivery = EmailDelivery(
                        user_id=setting.user_id,
                        schedule_id=schedule.id,
                        event_key=event_key,
                        kind="near",
                        applicable_date_vietnam=vn_date,
                        due_at_utc=due_utc,
                        status="pending",
                    )
                    db.add(delivery)
                    created.append(delivery)

        changed_schedules = db.scalars(
            select(Schedule).where(
                Schedule.user_id == setting.user_id,
                Schedule.version >= 2,
                or_(
                    Schedule.starts_at_utc > now,
                    Schedule.cancelled_at_utc >= now - UPDATE_LOOKBACK,
                ),
            )
        ).all()
        for schedule in changed_schedules:
            event_key = _update_event_key(schedule)
            existing = db.scalar(
                select(EmailDelivery).where(EmailDelivery.event_key == event_key)
            )
            if existing is None:
                vn_date = schedule.starts_at_utc.astimezone(VIETNAM_TZ).date()
                delivery = EmailDelivery(
                    user_id=setting.user_id,
                    schedule_id=schedule.id,
                    event_key=event_key,
                    kind="update",
                    applicable_date_vietnam=vn_date,
                    due_at_utc=now,
                    status="pending",
                )
                db.add(delivery)
                created.append(delivery)

    if created:
        db.flush()
    return created


def _compose_delivery_email(
    db: Session, delivery: EmailDelivery
) -> tuple[str, str]:
    if delivery.kind == "daily":
        target_date = (
            delivery.applicable_date_vietnam
            or delivery.due_at_utc.astimezone(VIETNAM_TZ).date()
        )
        start_utc = datetime.combine(
            target_date, time.min, tzinfo=VIETNAM_TZ
        ).astimezone(timezone.utc)
        end_utc = datetime.combine(
            target_date + timedelta(days=1), time.min, tzinfo=VIETNAM_TZ
        ).astimezone(timezone.utc)
        schedules = db.scalars(
            select(Schedule)
            .where(
                Schedule.user_id == delivery.user_id,
                Schedule.cancelled_at_utc.is_(None),
                Schedule.starts_at_utc >= start_utc,
                Schedule.starts_at_utc < end_utc,
            )
            .order_by(Schedule.starts_at_utc, Schedule.id)
        ).all()
        subject = f"[Secretary Schedule] Daily schedule summary ({target_date.isoformat()})"
        if not schedules:
            body = (
                f"You have no scheduled meetings on {target_date.isoformat()} "
                "(Asia/Ho_Chi_Minh)."
            )
        else:
            lines = [
                f"Your schedule summary for {target_date.isoformat()} (Asia/Ho_Chi_Minh):",
                "",
            ]
            for idx, item in enumerate(schedules, start=1):
                lines.append(
                    f"{idx}. {item.title} — {_format_vn_datetime(item.starts_at_utc)} "
                    f"({item.meeting_mode}, related: {item.related_person})"
                )
            body = "\n".join(lines)
        return subject, body

    schedule = (
        db.get(Schedule, delivery.schedule_id, populate_existing=True)
        if delivery.schedule_id is not None
        else None
    )
    if schedule is None:
        raise RuntimeError("Associated schedule not found")

    if delivery.kind == "near":
        subject = f"[Secretary Schedule] Upcoming: {schedule.title}"
        lines = [
            f"Reminder for your upcoming schedule: {schedule.title}",
            f"Starts at: {_format_vn_datetime(schedule.starts_at_utc)}",
            f"Ends at: {_format_vn_datetime(schedule.ends_at_utc)}",
            f"Mode: {schedule.meeting_mode}",
            f"Related person: {schedule.related_person}",
        ]
        if schedule.location:
            lines.append(f"Location: {schedule.location}")
        if schedule.meeting_url:
            lines.append(f"Meeting URL: {schedule.meeting_url}")
        if schedule.preparation:
            lines.append(f"Preparation: {schedule.preparation}")
        if schedule.notes:
            lines.append(f"Notes: {schedule.notes}")
        return subject, "\n".join(lines)

    status_label = (
        "Cancelled" if schedule.cancelled_at_utc is not None else "Updated"
    )
    subject = f"[Secretary Schedule] {status_label}: {schedule.title}"
    lines = [
        f"Your schedule '{schedule.title}' was {status_label.lower()} (version {schedule.version}).",
        f"Starts at: {_format_vn_datetime(schedule.starts_at_utc)}",
        f"Ends at: {_format_vn_datetime(schedule.ends_at_utc)}",
        f"Mode: {schedule.meeting_mode}",
        f"Related person: {schedule.related_person}",
    ]
    return subject, "\n".join(lines)


def process_due_deliveries(
    db: Session,
    *,
    now_utc: datetime | None = None,
    retry_delay: timedelta = DEFAULT_RETRY_DELAY,
    send_fn: SendEmailFn = send_email_message,
) -> list[EmailDelivery]:
    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    candidates = db.scalars(
        select(EmailDelivery)
        .where(
            EmailDelivery.due_at_utc <= now,
            EmailDelivery.status.in_(("pending", "failed")),
        )
        .order_by(EmailDelivery.due_at_utc, EmailDelivery.id)
    ).all()

    processed: list[EmailDelivery] = []
    for delivery in candidates:
        attempts = db.scalars(
            select(EmailAttempt)
            .where(EmailAttempt.delivery_id == delivery.id)
            .order_by(EmailAttempt.attempt_number)
        ).all()
        if len(attempts) >= MAX_ATTEMPTS:
            continue

        if delivery.status == "failed" and attempts:
            last_time = attempts[-1].finished_at_utc or attempts[-1].started_at_utc
            if now < last_time + retry_delay:
                continue

        if delivery.kind in ("near", "update"):
            schedule = (
                db.get(Schedule, delivery.schedule_id, populate_existing=True)
                if delivery.schedule_id is not None
                else None
            )
            if delivery.kind == "near":
                is_stale = (
                    schedule is None
                    or schedule.cancelled_at_utc is not None
                    or delivery.event_key != _near_event_key(schedule)
                )
            else:
                is_stale = (
                    schedule is None
                    or delivery.event_key != _update_event_key(schedule)
                )
            if is_stale:
                delivery.status = "cancelled"
                db.flush()
                processed.append(delivery)
                continue

        user = db.get(User, delivery.user_id)
        if user is None:
            delivery.status = "cancelled"
            processed.append(delivery)
            continue

        attempt_number = len(attempts) + 1
        attempt = EmailAttempt(
            delivery_id=delivery.id,
            attempt_number=attempt_number,
            status="sending",
            started_at_utc=now,
        )
        delivery.status = "sending"
        db.add(attempt)
        db.flush()

        try:
            subject, body = _compose_delivery_email(db, delivery)
            send_fn(to_email=user.email, subject=subject, body=body)
            finished_at = (
                now
                if now_utc is not None
                else max(now, datetime.now(timezone.utc))
            )
            attempt.status = "success"
            attempt.finished_at_utc = finished_at
            attempt.error_message = None
            delivery.status = "sent"
            delivery.sent_at_utc = finished_at
        except Exception as exc:
            finished_at = (
                now
                if now_utc is not None
                else max(now, datetime.now(timezone.utc))
            )
            attempt.status = "failed"
            attempt.finished_at_utc = finished_at
            attempt.error_message = f"{type(exc).__name__}: {exc}"[:500]
            delivery.status = "failed"

        db.flush()
        processed.append(delivery)

    return processed


def run_reminder_cycle(
    db: Session,
    *,
    now_utc: datetime | None = None,
    retry_delay: timedelta = DEFAULT_RETRY_DELAY,
    send_fn: SendEmailFn = send_email_message,
) -> list[EmailDelivery]:
    enqueue_due_reminders(db, now_utc=now_utc)
    processed = process_due_deliveries(
        db,
        now_utc=now_utc,
        retry_delay=retry_delay,
        send_fn=send_fn,
    )
    db.commit()
    return processed
