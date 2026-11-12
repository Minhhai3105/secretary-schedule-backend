# Local LiteLLM gateway

The path is browser → FastAPI → LiteLLM → CLIProxyAPI → model. Only FastAPI handles user accounts and schedules. LiteLLM maps the stable model name `secretary-schedule` to the upstream model and keeps its upstream credential on the server. The browser never receives either gateway or upstream key.

Install the gateway in its own environment, separate from the backend dependencies:

```sh
python3.11 -m venv gateway/.venv
gateway/.venv/bin/python -m pip install -r gateway/requirements.txt
```

With CLIProxyAPI running on `127.0.0.1:8317`, set these variables in the gateway terminal. Use the actual local CLIProxyAPI client key and a new long random LiteLLM master key; do not put either value in Git:

```sh
export CLIPROXY_BASE_URL=http://127.0.0.1:8317/v1
export CLIPROXY_API_KEY='your-local-CLIProxyAPI-client-key'
export LITELLM_MASTER_KEY='sk-your-long-random-gateway-key'
gateway/.venv/bin/litellm --config gateway/config.yaml --host 127.0.0.1 --port 4000
```

Set the backend environment to `AI_BASE_URL=http://127.0.0.1:4000/v1`, `AI_MODEL=secretary-schedule`, and `AI_API_KEY` equal to `LITELLM_MASTER_KEY`, then restart FastAPI. Test `POST /api/chat/messages` through the normal authenticated web flow. Missing schedule details should produce a clarifying answer. A complete request should produce a proposal that still requires confirmation.

This configuration does not use a LiteLLM database: it has one master key and does not provide virtual keys, per-user quotas, or spend budgets. Keep the gateway bound to loopback for local work. In deployment, run CLIProxyAPI and LiteLLM as separately managed services accessible to FastAPI, use HTTPS and service-managed secrets, and replace the localhost URLs with reachable private addresses. The local CLIProxyAPI account/session and provider access still need a permitted production setup.
