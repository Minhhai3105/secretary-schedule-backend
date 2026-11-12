# Secretary Schedule Backend

FastAPI + PostgreSQL backend for a multi-user schedule assistant. The database uses UUID primary keys and Alembic migrations.

## Local setup

Use Python 3.14 and PostgreSQL 18. Install the pinned packages in `requirements.txt` in a virtual environment. Set `DATABASE_URL` to a PostgreSQL URL for a database owned by the application role; set `PGPASSFILE` if using a password file. Set a random `JWT_SECRET` of at least 32 characters. Never commit these values.

Copy `.env.example` to `.env` and replace the placeholders. The app reads environment variables; it does not load `.env` automatically. In a local shell, run `set -a; . ./.env; set +a` before the commands below. Keep `.env` untracked.

```sh
./.venv/bin/python -m pip install -r requirements.txt
./.venv/bin/python -m alembic upgrade head
./.venv/bin/python -m uvicorn app.main:app --reload
```

The last command is for local HTTP only. Keep `AUTH_COOKIE_SECURE=1` with HTTPS in deployment. Open `/docs` for OpenAPI and `/health` for the health check.

Run the PostgreSQL-backed flow tests with `DATABASE_URL` set (and `PGPASSFILE` if your PostgreSQL setup uses a password file):

```sh
./.venv/bin/python -m unittest discover -s tests -v
./.venv/bin/python -m unittest tests.test_schedules -v
./.venv/bin/python -m unittest tests.test_chat_proposals -v
./.venv/bin/python -m unittest tests.test_reminders -v
./.venv/bin/python -m unittest tests.test_password_reset -v
```

Run the email reminder worker once or continuously:

```sh
./.venv/bin/python -m app.worker --once
./.venv/bin/python -m app.worker --interval 30
```

Run one continuous worker process alongside FastAPI in deployment; `--interval 30` polls every 30 seconds. `--once` is for a manual check or an external scheduler such as cron, not a test-only mode. The current worker does not coordinate multiple concurrent instances.

You can also run one case by its full name, for example `./.venv/bin/python -m unittest tests.test_schedules.ScheduleTests.test_stale_version_cannot_update_schedule -v`.

The five pre-merge development migrations were consolidated into `001_initial_schema.py`. A fresh database uses `alembic upgrade head`. An existing development database at the former revision must have its schema checked before its revision marker is changed to `001`; this does not recreate application tables. When adding later migrations, use the current head as `down_revision`. Numbered filenames alone do not prevent conflicts when two branches create migrations in parallel.

## External services

- Chat: set `AI_API_KEY`; optional `AI_BASE_URL` (default `http://127.0.0.1:8317/v1`), `AI_MODEL` (default `gemini-3.8-flash-high`), and `AI_TIMEOUT_SECONDS` (default 60). The base URL must be reachable from the running backend. The local CLIProxyAPI address will not work on a remote server unless that service is deployed there too.
- Password reset & reminders: set `SMTP_HOST`, `SMTP_USERNAME`, and `SMTP_PASSWORD`; optional `SMTP_PORT` (default 465), `SMTP_FROM`, and `SMTP_TIMEOUT_SECONDS` (default 30). Password reset also needs `APP_FRONTEND_URL`, an HTTP(S) frontend base URL used only to build the link in the email. The backend does not call the frontend. The reset endpoint returns 503 when its required configuration is missing or the frontend URL is invalid.
- Frontend: set `CORS_ORIGINS` to a comma-separated list of exact frontend origins. Local Vite defaults are `http://localhost:5173,http://127.0.0.1:5173`.

See [backend_status.md](docs/backend_status.md) for the implemented API and known limits.
