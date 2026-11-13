# SSE verification through real HTTP

This is verification tooling, not production streaming code. It uses a disposable PostgreSQL schema, two synthetic users, a temporary upstream HTTP server, a real Uvicorn instance of the existing application, and the installed frontend AI SDK through Vite SSR. It does not access existing user data or change project configuration. No dependencies are added.

## Controlled causal test

The upstream fixture sends an incomplete JSON response containing answer `A`, flushes an HTTP/1.1 chunk and withholds `B` behind an event gate. The real frontend `streamChatMessage` callback must receive `A` while the upstream response remains incomplete. Only that callback may release the gate through the probe control endpoint. The upstream then sends `B`, `stop` and `[DONE]`. The final answer must be `A B` with no proposal.

Passing requires this causal sequence, not simply multiple output chunks:

`upstream sends A -> BE reads/yields A -> frontend SDK callback receives A -> frontend releases gate -> upstream sends B -> frontend accepts final result`.

The initial SSE header/prefix is not treated as an answer. The fixture uses real sockets and the existing parser/business/persistence paths. The GUI renderer is not part of this test: it proves arrival at the real FE service callback, not a browser paint time.

## Negative control

`--negative-control` deliberately calls upstream `read()` inside the opt-in probe wrapper. The production application is unchanged. This buffers the response while the upstream waits for FE to see A. The gate must time out, and the normal streaming test must fail. The negative-control command exits successfully only if that expected failure is detected. A PASS for this negative control means buffering was detected, not that the buffered implementation streamed correctly.

## Live model measurement

Live mode calls the configured CLIProxyAPI/model once using only a synthetic message and account. It records provider-request time, response-header arrival, incoming content chunks, BE answer yields, outgoing HTTP bodies, FE wire chunks and FE answer callbacks. Message contents, JWTs, API keys and existing account data are not recorded.

Use durations within each process; wall-clock timestamps across Python and Node may differ by approximately a millisecond. The event gate establishes causality independently of that timing precision. A late first answer or a large number of callbacks arriving together is not enough to identify buffering. If no provider response headers arrive before timeout, the run cannot establish model streaming latency. It also cannot distinguish model latency from buffering/retries inside CLIProxyAPI without instrumentation of that service.

## Run locally

Check out both companion PR branches, install the backend requirements and run `npm ci` in the frontend. From the backend root, load its environment as usual, including `DATABASE_URL`, `PGPASSFILE` where required, and `AI_*`. The application database role must be able to create/drop temporary schemas, as in the migration tests.

```sh
python -m scripts.verify_sse --frontend /path/to/secretary-schedule-frontend \
  --mode controlled --output /tmp/sse-controlled.json

python -m scripts.verify_sse --frontend /path/to/secretary-schedule-frontend \
  --mode controlled --negative-control --output /tmp/sse-negative.json

python -m scripts.verify_sse --frontend /path/to/secretary-schedule-frontend \
  --mode live --output /tmp/sse-live.json
```

Controlled modes do not call an external model. Live mode makes one configured model call; there is no automatic retry. `--mode both` is the default. Optional `--upstream-timeout 120` changes the socket timeout only in the probe process, not in `.env` or the web server. The frontend retains its normal 180-second request timeout.

The frontend uses its existing `scripts/verify-sse.mjs`; the Vite cache is isolated in a temporary directory and removed afterward. Probe ports are allocated locally. The backend schema and servers are cleaned up after successful or failed probes. JSON reports include explicit PASS/FAIL results and metadata-only timelines. Retain failed live reports instead of reporting controlled-test results as proof that the model is always available.

## Observations on 2026-11-13

- Repeated controlled HTTP tests passed: FE observed A before B was released. Controlled runs observed A at approximately 84–102 ms after the FE request, including an HTTP/1.1 chunked run at 84 ms; that synthetic timing is not a model latency claim.
- The deliberately buffered negative control was detected: no timely FE answer arrived, the gate timed out and the normal streaming test failed as expected.
- Two live attempts failed before CLIProxyAPI response headers arrived, at configured 60-second and probe-only 120-second socket timeouts. No real-model token delivery latency can be claimed from those attempts.

Conclusion: the application path supports actual incremental upstream-to-FE delivery. The live provider's behavior and latency remain unverified for these timed-out attempts. Production stream code was not rewritten based on the review suspicion alone.
