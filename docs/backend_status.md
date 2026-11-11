# Backend status — 2026-11-05

## Database

Alembic migrations create nine tables: `users`, `schedules`, `auth_sessions`, `refresh_tokens`, `reminder_settings`, `password_resets`, `email_deliveries`, `email_attempts`, and `chat_proposals`. Primary keys are UUID. The local development database is at the consolidated Alembic head `001`; `alembic check` reports no pending model changes.

## API

- Auth: `POST /api/auth/register`, `/login`, `/refresh`, `/logout`, `/forgot-password`, `/reset-password`; `GET /api/auth/me`.
- Schedules: `GET/POST /api/schedules`; `GET /api/schedules/history`; `GET/PATCH /api/schedules/{schedule_id}`; `POST /api/schedules/{schedule_id}/cancel`.
- Reminder settings: `GET/PUT /api/reminder-settings`.
- Email audit: `GET /api/email-deliveries` and `GET /api/email-deliveries/{delivery_id}`.
- Chat: `POST /api/chat/messages`, `/api/chat/proposals/{proposal_id}/confirm`, `/api/chat/proposals/{proposal_id}/cancel`.

Every schedule, audit entry, and chat proposal is scoped to the authenticated user's UUID from a verified access token. The access token is a 15-minute JWT; refresh tokens are stored as hashes and rotated. Auth sessions currently expire after **30 days**. The design sheet previously suggested 7 days; confirm the product policy before release.

Chat sends up to five prior turns and at most 200 of the user's schedules to the configured OpenAI-compatible proxy. AI output is validated before a proposal is saved. A schedule is written only after a separate confirm call. The 200-schedule context limit means chat answers may omit older schedules; the schedule list/history APIs remain the authoritative way to browse all records.

## Verified locally

- PostgreSQL migrations applied; nine tables and constraints exist; Alembic model/schema comparison is clean.
- Auth register, login, refresh rotation and replay rejection, logout, `me`, password hashing, and permission checks were exercised in local development.
- Schedule, chat proposal, reminder worker, and password reset checks are split into PostgreSQL-backed test modules (`test_schedules`, `test_chat_proposals`, `test_reminders`, `test_password_reset`); test data is rolled back after each test.
- Email reminder worker (`app/services/reminders.py`, `app/worker.py`) enqueues `daily`, `near`, and `update` deliveries with idempotent `event_key`s, cancels obsolete `near`/`update` deliveries (`pending` or `failed`) when schedules advance to a newer version or are cancelled, refreshes schedule state from PostgreSQL (`populate_existing=True`) immediately before sending, and executes up to 4 `EmailAttempt`s per delivery.

## Not yet verified or implemented

- Real external SMTP delivery has not been verified with a live mail provider; unit tests use mocked SMTP (`SMTP_SSL` port 465 and `SMTP` STARTTLS port 587), and the password-reset background task is not durable across a process crash.
- The reminder worker is designed to run as a single process (`python -m app.worker`); multi-worker concurrent locking (`FOR UPDATE SKIP LOCKED`) is not yet implemented.
- The local CLIProxyAPI and its key are not packaged for deployment; production AI access needs a reachable, authorized provider and secret configuration.
- No load test has demonstrated the requested ten concurrent users. Rate limiting, deployment configuration, and frontend integration remain to be done.
