# Run the backend with Docker Compose

This is a local development/demo setup for Docker Desktop on macOS. It starts
FastAPI and a separate PostgreSQL 18 database. It does not import the database
installed on the Mac. CLIProxyAPI and the frontend run separately.

## First start

In a fresh checkout with no `.env`:

```sh
cp .env.docker.example .env
```

Generate three different secrets and fill `DB_ADMIN_PASSWORD`, `DB_PASSWORD`
and `JWT_SECRET` (at least 32 characters). Never commit the filled file:

```sh
python3 -c 'import secrets; print(secrets.token_hex(32))'
```

Set `AI_API_KEY` to your CLIProxyAPI client key and `AI_MODEL` to a model that
your proxy actually lists. An empty AI key leaves the other APIs usable but
chat returns 503. SMTP settings are optional until testing email features.

```sh
docker compose config --quiet
docker compose up --build -d --wait
curl --fail http://127.0.0.1:8000/health
docker compose exec api python -m alembic current
docker compose exec db psql -U postgres -d secretary_schedule_dev -c '\dt'
```

Expected: both services healthy, `{"status":"ok"}`, migration `003 (head)`,
and the application tables plus `alembic_version`. Open
<http://127.0.0.1:8000/docs> to register a new account and log in. Existing Mac
database accounts are not present in this separate database. The database has
no published host port. The API runs as a non-root user and connects with a
non-superuser PostgreSQL role. It waits for PostgreSQL health and applies
migrations before starting Uvicorn; migration failure stops API startup.

If a local `.env` already exists, preserve it: copy the template to
`.env.docker` and use `docker compose --env-file .env.docker ...` for **every**
Compose command instead. Do not source the local `.env` into this shell:
exported shell variables override values in the Compose environment file.
Compose supplies the container database URL; the local `DATABASE_URL` is not
used. Passwords are supplied separately, so URL encoding is unnecessary.

If port 8000 is occupied, set `API_PORT=8001` and use port 8001 in the browser
and frontend API configuration. Do not stop unrelated containers to free it.

## Check CLIProxyAPI from the container

Start/authenticate CLIProxyAPI separately on the Mac. `127.0.0.1` inside the
container refers to the container, so use
`AI_BASE_URL=http://host.docker.internal:8317/v1` on Docker Desktop.

```sh
docker compose exec -T api python - <<'PY'
import json
import os
from urllib.request import Request, urlopen

request = Request(
    os.environ['AI_BASE_URL'].rstrip('/') + '/models',
    headers={'Authorization': 'Bearer ' + os.environ['AI_API_KEY']},
)
with urlopen(request, timeout=10) as response:
    models = json.load(response)['data']
print('Models:', [model['id'] for model in models])
print('Configured model listed:', any(model['id'] == os.environ['AI_MODEL'] for model in models))
PY
```

This verifies model discovery, not chat or structured-output support. Also
test a real authenticated chat from the frontend: streamed answer, complete
proposal, confirmation, and schedule visible in the week view. If connection
fails, verify the proxy is running and reachable from Docker Desktop. Do not
expose the proxy publicly or disable its authentication to work around it.
If discovery succeeds but chat fails, inspect the backend/provider status;
provider quota or model errors remain possible.

## Persistence and daily commands

```sh
docker compose logs --tail=50 api
docker compose restart api
docker compose down
docker compose up -d --wait
```

`down` keeps the named PostgreSQL volume. Verify a previously registered
account can still log in after `down` / `up`. **Do not use `down -v`** unless
you intentionally want to delete this Docker database. The initialization
script runs only on an empty volume: changing password variables later does
not change PostgreSQL role passwords; rotate them in PostgreSQL as well.
For config changes use `docker compose up -d --force-recreate api`, because
`restart` does not reload container environment variables.

One optional reminder worker can run from the same image after migrations:

```sh
docker compose --profile worker up -d --wait
```

Or as a one-off container:

```sh
docker compose run --rm --no-deps api python -m scripts.reminder_worker --interval 30
```

## Full-stack Docker Compose (FE + BE + PostgreSQL + Worker)

When the companion `secretary-schedule-frontend` repository is checked out next
to this repo (`../secretary-schedule-frontend`, or set `FRONTEND_CONTEXT` in
your Compose env file), launch all four services (`db`, `api`, `worker`, `web`)
together:

```sh
docker compose -f compose.yaml -f compose.fullstack.yaml --env-file .env.docker config --quiet
docker compose -f compose.yaml -f compose.fullstack.yaml --env-file .env.docker up --build -d --wait
curl --fail http://127.0.0.1:${API_PORT:-8000}/health
curl --fail http://127.0.0.1:${FRONTEND_PORT:-5173}/healthz
```

Open <http://localhost:5173> (or `FRONTEND_PORT` if customized). If you change
`API_PORT`, rebuild `web` (`--build`) so `VITE_API_BASE_URL` matches the host
API port, and keep `CORS_ORIGINS` / `APP_FRONTEND_URL` aligned with
`FRONTEND_PORT`.

## GitHub Actions CI

`.github/workflows/ci.yml` automatically runs on every push and pull request
targeting `main`:

- `test`: starts PostgreSQL 18, installs `requirements.txt`, verifies
  `pip check`, applies and checks Alembic migrations (`alembic upgrade head`
  and `alembic check`), and runs the full `unittest` suite.
- `docker`: validates `compose.yaml` (default and `worker` profiles) and builds
  the backend image.

The worker is not started automatically by this two-service setup; run only
one worker. This configuration has no HTTPS, backups, or deployment rollout
coordination. Before deployment configure those separately and enable secure
cookies behind HTTPS. `/health` is a process check, not a database/AI readiness
check. Python requirements are unchanged. PostgreSQL 18 mounts its volume at
`/var/lib/postgresql`, per the official image documentation:
<https://hub.docker.com/_/postgres>.

## Local acceptance evidence — 2026-11-16

Verified on Docker Desktop for macOS, from the dedicated Docker worktree,
using an ignored `.env.docker` and host API port 8001 (8000 was occupied):

| Check | Observed result |
| --- | --- |
| Compose build and startup on an empty volume | API and database healthy |
| HTTP `/health` | 200, `{"status":"ok"}` |
| Alembic | `003 (head)`; `alembic check` found no new operations |
| Tables | 10 application tables plus `alembic_version` |
| Registration, login, create schedule | HTTP 201, 200, 201 |
| Persistence after `down` then `up -d --wait` | Same account logged in; same schedule retrieved, both HTTP 200 |
| Container to Mac proxy | `/v1/models` HTTP 200; configured `gemini-3.8-flash-high` listed |
| Real chat through backend, `stream:false` | HTTP 200 with a valid structured answer, no proposal; provider not mocked |
| Existing backend test suite in container | 53 tests passed against Docker PostgreSQL |
| Image/runtime isolation | UID 10001; no `.env`, `.env.docker`, or `.local` in image; application DB role has no superuser/create-role/create-database privileges |

The unit suite emitted an existing unclosed-connection `ResourceWarning` after
passing; no application Python code was changed in this Docker task. These
checks do not claim browser rendering, live SSE timing, email delivery, or
production deployment acceptance. Live provider availability can change.
The optional reminder worker was not started.

Re-run the existing tests without copying them into the runtime image:

```sh
docker compose --env-file .env.docker run --rm --no-deps \
  -v "$PWD/tests:/app/tests:ro" api python -m unittest discover -s tests -v
```
