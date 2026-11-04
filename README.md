# Secretary Schedule Backend

FastAPI + PostgreSQL backend for a multi-user schedule assistant. The database uses UUID primary keys and Alembic migrations.

## Local setup

Use Python 3.14 and PostgreSQL 18. Install the pinned packages in `requirements.txt` in a virtual environment. Set `DATABASE_URL` to a PostgreSQL URL for a database owned by the application role; set `PGPASSFILE` if using a password file. Set a random `JWT_SECRET` of at least 32 characters. Never commit these values.

```sh
./.venv/bin/python -m pip install -r requirements.txt
PGPASSFILE="$PWD/.local/pgpass" ./.venv/bin/python -m alembic upgrade head
DATABASE_URL='postgresql+psycopg://secretary_schedule_app@/secretary_schedule_dev' PGPASSFILE="$PWD/.local/pgpass" JWT_SECRET="$(cat .local/jwt_secret)" AUTH_COOKIE_SECURE=0 ./.venv/bin/python -m uvicorn app.main:app --reload
```

The last command is for local HTTP only. Keep `AUTH_COOKIE_SECURE=1` with HTTPS in deployment. Open `/docs` for OpenAPI and `/health` for the health check.

Run the PostgreSQL-backed flow tests with `DATABASE_URL` and `PGPASSFILE` set:

```sh
DATABASE_URL='postgresql+psycopg://secretary_schedule_app@/secretary_schedule_dev' PGPASSFILE="$PWD/.local/pgpass" ./.venv/bin/python -m unittest discover -s tests -v
```

## External services

- Chat: set `AI_API_KEY`; optional `AI_BASE_URL` (default `http://127.0.0.1:8317/v1`), `AI_MODEL` (default `gemini-3.8-flash-high`), and `AI_TIMEOUT_SECONDS` (default 60). The base URL must be reachable from the running backend. The local CLIProxyAPI address will not work on a remote server unless that service is deployed there too.
- Password reset: set `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, and `APP_FRONTEND_URL`; optional `SMTP_PORT` (default 465) and `SMTP_FROM`. The reset endpoint returns 503 until these are set. Real SMTP delivery has not been tested.
- Frontend: set `CORS_ORIGINS` to a comma-separated list of exact frontend origins. Local Vite defaults are `http://localhost:5173,http://127.0.0.1:5173`.

See [backend_status.md](docs/backend_status.md) for the implemented API and known limits.
