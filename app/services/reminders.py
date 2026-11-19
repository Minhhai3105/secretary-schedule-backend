from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.email_attempt import EmailAttempt
from app.models.email_delivery import EmailDelivery
from app.models.reminder_setting import ReminderSetting
from app.models.schedule import Schedule
from app.models.user import User
from app.services.email_template import (
    render_daily_summary_html,
    render_near_reminder_html,
    render_update_notification_html,
)
from app.services.mailer import send_email_message

VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
MAX_ATTEMPTS = 4
DEFAULT_RETRY_DELAY = timedelta(minutes=5)
DEFAULT_BATCH_SIZE = 10
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
        select(EmailDelivery)
        .where(
            EmailDelivery.kind.in_(("near", "update")),
            EmailDelivery.status.in_(("pending", "failed")),
        )
        .with_for_update(skip_locked=True)
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


def _enqueue_delivery(
    db: Session,
    *,
    user_id: object,
    schedule_id: object | None,
    event_key: str,
    kind: str,
    applicable_date_vietnam: date | None,
    due_at_utc: datetime,
) -> EmailDelivery | None:
    # Fast MVCC check avoids triggering PostgreSQL ON CONFLICT transactionid waits
    # when another worker is currently updating this already-committed delivery.
    existing_id = db.scalar(
        select(EmailDelivery.id).where(EmailDelivery.event_key == event_key)
    )
    if existing_id is not None:
        return None
    stmt = (
        pg_insert(EmailDelivery)
        .values(
            user_id=user_id,
            schedule_id=schedule_id,
            event_key=event_key,
            kind=kind,
            applicable_date_vietnam=applicable_date_vietnam,
            due_at_utc=due_at_utc,
            status="pending",
        )
        .on_conflict_do_nothing(index_elements=["event_key"])
        .returning(EmailDelivery)
    )
    return db.scalar(stmt)


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
                delivery = _enqueue_delivery(
                    db,
                    user_id=setting.user_id,
                    schedule_id=None,
                    event_key=event_key,
                    kind="daily",
                    applicable_date_vietnam=vn_today,
                    due_at_utc=due_utc,
                )
                if delivery is not None:
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
                due_utc = schedule.starts_at_utc - timedelta(
                    minutes=setting.lead_minutes
                )
                vn_date = schedule.starts_at_utc.astimezone(VIETNAM_TZ).date()
                delivery = _enqueue_delivery(
                    db,
                    user_id=setting.user_id,
                    schedule_id=schedule.id,
                    event_key=event_key,
                    kind="near",
                    applicable_date_vietnam=vn_date,
                    due_at_utc=due_utc,
                )
                if delivery is not None:
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
            vn_date = schedule.starts_at_utc.astimezone(VIETNAM_TZ).date()
            delivery = _enqueue_delivery(
                db,
                user_id=setting.user_id,
                schedule_id=schedule.id,
                event_key=event_key,
                kind="update",
                applicable_date_vietnam=vn_date,
                due_at_utc=now,
            )
            if delivery is not None:
                created.append(delivery)

    if created:
        db.flush()
    return created


def _compose_delivery_email(
    db: Session, delivery: EmailDelivery
) -> tuple[str, str, str]:
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
            html_body = render_daily_summary_html(date_str=target_date.isoformat(), schedules=[])
        else:
            lines = [
                f"Your schedule summary for {target_date.isoformat()} (Asia/Ho_Chi_Minh):",
                "",
            ]
            schedules_data: list[dict[str, str | None]] = []
            for idx, item in enumerate(schedules, start=1):
                start_str = _format_vn_datetime(item.starts_at_utc)
                end_str = _format_vn_datetime(item.ends_at_utc) if item.ends_at_utc else ""
                time_range = f"{start_str} - {end_str}" if end_str else start_str
                lines.append(
                    f"{idx}. {item.title} — {start_str} "
                    f"({item.meeting_mode}, related: {item.related_person})"
                )
                schedules_data.append({
                    "title": item.title,
                    "time_range": time_range,
                    "mode": item.meeting_mode,
                    "related_person": item.related_person,
                    "location": item.location,
                    "meeting_url": item.meeting_url,
                })
            body = "\n".join(lines)
            html_body = render_daily_summary_html(date_str=target_date.isoformat(), schedules=schedules_data)
        return subject, body, html_body

    schedule = (
        db.get(Schedule, delivery.schedule_id, populate_existing=True)
        if delivery.schedule_id is not None
        else None
    )
    if schedule is None:
        raise RuntimeError("Associated schedule not found")

    start_str = _format_vn_datetime(schedule.starts_at_utc)
    end_str = _format_vn_datetime(schedule.ends_at_utc) if schedule.ends_at_utc else None

    if delivery.kind == "near":
        subject = f"[Secretary Schedule] Upcoming: {schedule.title}"
        lines = [
            f"Reminder for your upcoming schedule: {schedule.title}",
            f"Starts at: {start_str}",
            f"Ends at: {end_str or 'N/A'}",
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
        body = "\n".join(lines)
        html_body = render_near_reminder_html(
            title=schedule.title,
            starts_at=start_str,
            ends_at=end_str,
            meeting_mode=schedule.meeting_mode,
            related_person=schedule.related_person,
            location=schedule.location,
            meeting_url=schedule.meeting_url,
            preparation=schedule.preparation,
            notes=schedule.notes,
        )
        return subject, body, html_body

    is_cancelled = schedule.cancelled_at_utc is not None
    status_label = "Cancelled" if is_cancelled else "Updated"
    subject = f"[Secretary Schedule] {status_label}: {schedule.title}"
    lines = [
        f"Your schedule '{schedule.title}' was {status_label.lower()} (version {schedule.version}).",
        f"Starts at: {start_str}",
        f"Ends at: {end_str or 'N/A'}",
        f"Mode: {schedule.meeting_mode}",
        f"Related person: {schedule.related_person}",
    ]
    body = "\n".join(lines)
    html_body = render_update_notification_html(
        title=schedule.title,
        version=schedule.version,
        starts_at=start_str,
        ends_at=end_str,
        meeting_mode=schedule.meeting_mode,
        related_person=schedule.related_person,
        is_cancelled=is_cancelled,
    )
    return subject, body, html_body


def _claim_next_due_delivery(
    db: Session,
    *,
    now: datetime,
    retry_delay: timedelta,
    exclude_ids: set[object],
) -> EmailDelivery | None:
    attempt_count = (
        select(func.count(EmailAttempt.id))
        .where(EmailAttempt.delivery_id == EmailDelivery.id)
        .correlate(EmailDelivery)
        .scalar_subquery()
    )
    latest_attempt_time = (
        select(
            func.max(
                func.coalesce(
                    EmailAttempt.finished_at_utc,
                    EmailAttempt.started_at_utc,
                )
            )
        )
        .where(EmailAttempt.delivery_id == EmailDelivery.id)
        .correlate(EmailDelivery)
        .scalar_subquery()
    )
    stmt = (
        select(EmailDelivery)
        .where(
            EmailDelivery.due_at_utc <= now,
            EmailDelivery.status.in_(("pending", "failed")),
            attempt_count < MAX_ATTEMPTS,
            or_(
                EmailDelivery.status != "failed",
                latest_attempt_time.is_(None),
                latest_attempt_time <= now - retry_delay,
            ),
        )
        .order_by(EmailDelivery.due_at_utc, EmailDelivery.id)
        .with_for_update(skip_locked=True, of=EmailDelivery)
        .limit(1)
    )
    if exclude_ids:
        stmt = stmt.where(EmailDelivery.id.notin_(exclude_ids))
    return db.scalar(stmt)


def process_due_deliveries(
    db: Session,
    *,
    now_utc: datetime | None = None,
    retry_delay: timedelta = DEFAULT_RETRY_DELAY,
    send_fn: SendEmailFn = send_email_message,
    batch_size: int = DEFAULT_BATCH_SIZE,
    commit_each: bool = False,
) -> list[EmailDelivery]:
    if batch_size <= 0:
        return []

    now = (now_utc or datetime.now(timezone.utc)).astimezone(timezone.utc)
    processed: list[EmailDelivery] = []
    seen_ids: set[object] = set()

    for _ in range(batch_size):
        delivery = _claim_next_due_delivery(
            db,
            now=now,
            retry_delay=retry_delay,
            exclude_ids=seen_ids,
        )
        if delivery is None:
            break
        seen_ids.add(delivery.id)

        attempts = db.scalars(
            select(EmailAttempt)
            .where(EmailAttempt.delivery_id == delivery.id)
            .order_by(EmailAttempt.attempt_number)
        ).all()
        if len(attempts) >= MAX_ATTEMPTS:
            if commit_each:
                db.commit()
            continue

        if delivery.status == "failed" and attempts:
            last_time = attempts[-1].finished_at_utc or attempts[-1].started_at_utc
            if now < last_time + retry_delay:
                if commit_each:
                    db.commit()
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
                if commit_each:
                    db.commit()
                processed.append(delivery)
                continue

        user = db.get(User, delivery.user_id)
        if user is None:
            delivery.status = "cancelled"
            db.flush()
            if commit_each:
                db.commit()
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
            subject, body, html_body = _compose_delivery_email(db, delivery)
            send_fn(
                to_email=user.email,
                subject=subject,
                body=body,
                html_body=html_body,
            )
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
        if commit_each:
            db.commit()
        processed.append(delivery)

    return processed


def run_reminder_cycle(
    db: Session,
    *,
    now_utc: datetime | None = None,
    retry_delay: timedelta = DEFAULT_RETRY_DELAY,
    send_fn: SendEmailFn = send_email_message,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[EmailDelivery]:
    enqueue_due_reminders(db, now_utc=now_utc)
    db.commit()
    return process_due_deliveries(
        db,
        now_utc=now_utc,
        retry_delay=retry_delay,
        send_fn=send_fn,
        batch_size=batch_size,
        commit_each=True,
    )
