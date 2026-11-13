# Secretary Schedule Backend

FastAPI + PostgreSQL backend for a multi-user schedule assistant. The database uses UUID primary keys and Alembic migrations.

## Local setup

Use Python 3.14 and PostgreSQL 18. Install the pinned packages in `requirements.txt` in a virtual environment. Set `DATABASE_URL` to a PostgreSQL URL for a database owned by the application role; set `PGPASSFILE` if using a password file. Set a random `JWT_SECRET` of at least 32 characters. Never commit these values.

Copy `.env.example` to `.env` and replace the placeholders. The app reads environment variables; it does not load `.env` automatically. In a local shell, run `set -a; . ./.env; set +a` before the commands below. Keep `.env` untracked.

```sh
python3.14 -m venv .venv
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
./.venv/bin/python -m unittest tests.test_chat_history -v
./.venv/bin/python -m unittest tests.test_chat_completions -v
./.venv/bin/python -m unittest tests.test_migrations -v
```

Run the email reminder worker once or continuously:

```sh
./.venv/bin/python -m scripts.reminder_worker --once
./.venv/bin/python -m scripts.reminder_worker --interval 30
```

Run one continuous worker process alongside FastAPI in deployment; `--interval 30` polls every 30 seconds. `--once` is for a manual check or an external scheduler such as cron, not a test-only mode. The current worker does not coordinate multiple concurrent instances.

You can also run one case by its full name, for example `./.venv/bin/python -m unittest tests.test_schedules.ScheduleTests.test_stale_version_cannot_update_schedule -v`.

The five pre-merge development migrations were consolidated into `001_initial_schema.py`. A fresh database uses `alembic upgrade head`. An existing development database at the former revision must have its schema checked before its revision marker is changed to `001`; this does not recreate application tables. When adding later migrations, use the current head as `down_revision`. Numbered filenames alone do not prevent conflicts when two branches create migrations in parallel.

The migration chain is `001_initial_schema.py` → `002_related_person_me.py` → `003_chat_messages.py`, with a single head `003`. Earlier branches accidentally gave both later migrations the revision `002`. Revision `003` supports both known legacy states: it creates the chat table when absent, or preserves existing chat messages and applies the `Me` default if the former chat migration already ran as `002`. A database already at `003` needs no revision change. Run `alembic upgrade head`; do not reset the database or blindly stamp a different revision to fix this mismatch. Uvicorn startup and `/health` do not run migrations or verify the database schema. Migration tests use disposable PostgreSQL schemas and require permission to create and drop those test schemas.

Migration `003_chat_messages.py` adds account-scoped chat history. `GET /api/chat/messages?limit=20&cursor=<message UUID>` returns newest messages first, at most 50 per page, and a `next_cursor` for older messages. `POST /api/chat/messages` now loads the five most recent turns from PostgreSQL as AI context. The legacy request `history` field remains accepted for older clients but is ignored. `PATCH /api/reminder-settings` updates only supplied settings; the existing full-replacement `PUT` remains available.

## External services

The web chat uses authenticated `POST /v1/chat/completions` with `messages`, a fixed structured response schema, and the standard `choices[].message.content` envelope. See [chat-completions.md](docs/chat-completions.md) for the supported subset, example requests, error handling, and frontend schema snapshot.

For a real-HTTP, gated A/B streaming check and a separate live-provider measurement, see [sse-verification.md](docs/sse-verification.md). These probes use temporary schemas and synthetic data, and include a negative control that detects intentional upstream buffering.

- Chat: FastAPI calls CLIProxyAPI directly at `AI_BASE_URL` (default `http://127.0.0.1:8317/v1`) using `AI_API_KEY` (the CLIProxyAPI client key), `AI_MODEL` (default `gemini-3.8-flash-high`), and `AI_TIMEOUT_SECONDS` (default 120). LiteLLM is not required. Start and authenticate CLIProxyAPI separately, select a model available from its `/v1/models` endpoint, and keep its client key on the backend. The backend sends a strict JSON Schema through the OpenAI-compatible Chat Completions API, validates the response, and requires proposal confirmation before changing schedules. Model/schema support must be verified for the configured provider; protocol compatibility alone does not guarantee every structured-output feature. If the key is empty, chat returns 503 while the other APIs remain available. In deployment, the configured URL must be reachable from FastAPI; localhost refers to the machine or container running FastAPI, not the developer's computer.
- Password reset & reminders: set `SMTP_HOST`, `SMTP_USERNAME`, and `SMTP_PASSWORD`; optional `SMTP_PORT` (default 465), `SMTP_FROM`, and `SMTP_TIMEOUT_SECONDS` (default 30). Password reset also needs `APP_FRONTEND_URL`, an HTTP(S) frontend base URL used only to build the link in the email. The backend does not call the frontend. The reset endpoint returns 503 when its required configuration is missing or the frontend URL is invalid.
- Frontend: set `CORS_ORIGINS` to a comma-separated list of exact frontend origins. Local Vite defaults are `http://localhost:5173,http://127.0.0.1:5173`.

See [backend_status.md](docs/backend_status.md) for the implemented API and known limits.

## Troubleshooting chat 502 on another machine

Pulling code does not replace an existing `.env`. For the direct CLIProxyAPI setup, check `AI_BASE_URL=http://127.0.0.1:8317/v1`, `AI_MODEL=gemini-3.8-flash-high` (or a model listed by that server), and `AI_API_KEY` equal to that CLIProxyAPI server's client key. A previous LiteLLM URL on port 4000, its `secretary-schedule` alias, or its master key must be replaced for this setup. `127.0.0.1` refers to the machine/container running FastAPI, so CLIProxyAPI must be running there or the URL must point to a reachable server. Re-load `.env` and restart FastAPI after editing it.

With the environment loaded, check the provider connection without printing the key:

```sh
curl --max-time 10 -sS -o /dev/null -w 'HTTP %{http_code}\n' \
  -H "Authorization: Bearer $AI_API_KEY" "$AI_BASE_URL/models"
```

HTTP 200 verifies authentication to the model-list endpoint only; an actual chat request is still required to verify model availability and schema support. Connection failure means the server address/service is unavailable; HTTP 401/403 means authentication/access must be checked; HTTP 404 means the base URL/path must be checked. Provider quota/model errors may occur only on chat requests. FastAPI logs the upstream HTTP status or connection/response error class for chat failures, without logging credentials, message content, or provider error bodies. Share those diagnostic lines, not the `.env` or API key, when reporting a failure.
