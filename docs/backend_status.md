# Backend status — 2026-11-04

## Database

Alembic migrations create nine tables: `users`, `schedules`, `auth_sessions`, `refresh_tokens`, `reminder_settings`, `password_resets`, `email_deliveries`, `email_attempts`, and `chat_proposals`. Primary keys are UUID. The local development database is at Alembic head `f75920e8d4d3`; `alembic check` reports no pending model changes.

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
- Schedule create/list/history/detail/edit/cancel, settings, email audit, password reset, and chat proposal transactions were exercised against PostgreSQL; test data was rolled back.
- Real HTTP requests returned 200 for login, schedule list, reminder settings, and email audit, and 401 for an unauthenticated schedule list.
- The local AI proxy returned a JSON question for a missing-time chat request. A complete-data request failed once at the previous 30-second limit and again with a 502 response after raising the timeout to 60 seconds. This AI path is not yet accepted as reliable.

## Not yet verified or implemented

- SMTP credentials and a real password reset email are not configured; the email background task is not durable across a process crash.
- The email reminder worker that creates deliveries, sends mail, and retries failed attempts is not implemented. The audit API reads data if delivery rows exist.
- The local CLIProxyAPI and its key are not packaged for deployment; production AI access needs a reachable, authorized provider and secret configuration.
- No load test has demonstrated the requested ten concurrent users. Rate limiting, deployment configuration, and frontend integration remain to be done.
