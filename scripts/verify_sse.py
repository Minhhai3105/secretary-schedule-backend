"""Verify upstream -> real FastAPI HTTP -> real frontend AI SDK without production data.

Run with DATABASE_URL/PGPASSFILE/AI_* configured, plus --frontend /path/to/frontend.
Only a disposable PostgreSQL schema is modified. No tokens, keys or message text are recorded.
"""
import argparse
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
import uvicorn


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('both', 'controlled', 'live'), default='both')
    parser.add_argument('--negative-control', action='store_true',
                        help='Controlled mode only: deliberately buffer upstream in the probe to verify detection')
    parser.add_argument('--upstream-timeout', type=int,
                        help='Optional probe-only socket timeout; does not change project configuration')
    args = parser.parse_args()
    if args.negative_control and args.mode != 'controlled':
        parser.error('--negative-control requires --mode controlled')
    if args.upstream_timeout is not None:
        if not 1 <= args.upstream_timeout <= 150:
            parser.error('--upstream-timeout must be between 1 and 150 seconds')
        os.environ['AI_TIMEOUT_SECONDS'] = str(args.upstream_timeout)
    frontend = args.frontend.resolve()
    if not (frontend / 'scripts/verify-sse.mjs').is_file():
        parser.error('The frontend checkout must contain scripts/verify-sse.mjs and installed dependencies')
    actual_url = os.environ.get('AI_BASE_URL', 'http://127.0.0.1:8317/v1')
    actual_key = os.environ.get('AI_API_KEY', '')
    admin = create_engine(os.environ['DATABASE_URL'])
    schema = 'sse_verify_' + uuid4().hex
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    os.environ.update(
        DATABASE_URL=make_url(os.environ['DATABASE_URL']).update_query_dict(
            {'options': '-csearch_path=' + schema}).render_as_string(hide_password=False),
        JWT_SECRET=secrets.token_hex(32), AUTH_COOKIE_SECURE='0',
        SMTP_HOST='', SMTP_USERNAME='', SMTP_PASSWORD='',
    )
    state = {'mode': 'controlled', 'events': [], 'results': [], 'upstream_completed': False}
    gate = threading.Event()
    lock = threading.Lock()

    def record(event, **extra):
        with lock:
            state['events'].append({'mode': state['mode'], 'event': event,
                                    'at_ms': time.time_ns() / 1_000_000, **extra})

    class ProbeHandler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass  # Do not emit HTTP credentials or synthetic request bodies.

        def reply(self, body):
            data = json.dumps(body).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path != '/probe/state':
                self.send_error(404)
                return
            self.reply({'released': gate.is_set(), 'upstream_completed': state['upstream_completed']})

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
            if self.path == '/probe/start':
                mode = data['mode']
                if mode not in ('controlled', 'live'):
                    self.send_error(400)
                    return
                state.update(mode=mode, upstream_completed=False)
                gate.clear()
                os.environ['AI_BASE_URL'] = (probe_url + '/v1') if mode == 'controlled' else actual_url
                os.environ['AI_API_KEY'] = 'probe-only' if mode == 'controlled' else actual_key
                record('mode_start')
                self.reply({'mode': mode})
            elif self.path == '/probe/release':
                record('fe_observed_A', observed_at_ms=data['observed_at_ms'])
                record('release_B')
                gate.set()
                self.reply({'released': True})
            elif self.path == '/probe/events':
                with lock:
                    state['events'].extend({'mode': data['mode'], **event} for event in data['events'])
                self.reply({'saved': True})
            elif self.path == '/probe/results':
                state['results'] = data
                self.reply({'saved': True})
            elif self.path == '/v1/chat/completions':
                if data.get('stream') is not True:
                    self.send_error(400)
                    return
                self.send_response(200)
                self.send_header('Content-Type', 'text/event-stream')
                self.send_header('Transfer-Encoding', 'chunked')
                self.send_header('Connection', 'close')
                self.end_headers()

                def write_chunk(data):
                    self.wfile.write(f'{len(data):X}\r\n'.encode() + data + b'\r\n')
                    self.wfile.flush()

                def emit(content=None, finish=None):
                    chunk = {'choices': [{'index': 0, 'delta': {} if content is None else {'content': content},
                                          'finish_reason': finish}]}
                    write_chunk(('data: ' + json.dumps(chunk) + '\n\n').encode())

                record('controlled_write_A')
                emit('{"intent":"answer","answer":"A')
                if not gate.wait(timeout=5 if args.negative_control else 15):
                    record('gate_timeout_no_FE_answer')
                    self.close_connection = True
                    return
                record('controlled_write_B')
                emit(' B","schedule_ids":[],"schedule_id":null,"schedule_fields":[]}')
                emit(finish='stop')
                write_chunk(b'data: [DONE]\n\n')
                self.wfile.write(b'0\r\n\r\n')
                self.wfile.flush()
                state['upstream_completed'] = True
                self.close_connection = True
            else:
                self.send_error(404)

    control = ThreadingHTTPServer(('127.0.0.1', 0), ProbeHandler)
    control.daemon_threads = True
    probe_url = f'http://127.0.0.1:{control.server_port}'
    server = None
    control_thread = None
    thread = None
    try:
        command.upgrade(Config('alembic.ini'), 'head')
        from app.core.security import hash_password
        from app.db.session import engine
        from app.main import app
        from app.api import chat
        from app.services import chat_ai
        with engine.begin() as connection:
            for mode in ('controlled', 'live'):
                connection.execute(text('INSERT INTO users(id,name,email,password_hash) VALUES (:id,:name,:email,:hash)'),
                    {'id': uuid4(), 'name': 'SSE verification', 'email': f'sse-{mode}@example.com',
                     'hash': hash_password('Sse-probe-only-123')})

        original_open = chat_ai.urlopen

        class TracedResponse:
            def __init__(self, response):
                self.response = response
                self.headers = response.headers

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.response.close()

            def readline(self, limit):
                line = self.response.readline(limit)
                if line.startswith(b'data:'):
                    value = line[5:].strip()
                    if value == b'[DONE]':
                        record('be_upstream_DONE')
                    else:
                        chunk = json.loads(value)
                        choices = chunk.get('choices', [])
                        piece = choices[0].get('delta', {}).get('content') if choices else None
                        if piece:
                            record('be_upstream_content', chars=len(piece))
                        else:
                            record('be_upstream_event')
                return line

        negative_control = args.negative_control

        def traced_open(request, *, timeout):
            record('be_provider_request')
            result = original_open(request, timeout=timeout)
            record('be_provider_headers', status=result.status)
            if negative_control:
                record('negative_read_all_started')
                body = result.read()  # Deliberately wrong, only inside this opt-in negative-control probe.
                headers = result.headers
                result.close()
                result = BytesIO(body)
                result.headers = headers
            return TracedResponse(result)

        original_stream = chat.stream_schedule_ai

        def traced_stream(message, history, schedules, now_vietnam):
            source = original_stream(message, history, schedules, now_vietnam)
            try:
                for kind, value in source:
                    record('be_answer_yield' if kind == 'answer' else 'be_validated_result',
                           **({'chars': len(value)} if kind == 'answer' else {}))
                    yield kind, value
            finally:
                source.close()

        chat_ai.urlopen = traced_open
        chat.stream_schedule_ai = traced_stream

        async def traced_app(scope, receive, send):
            async def trace_send(message):
                if scope.get('path') == '/v1/chat/completions' and message['type'] == 'http.response.body':
                    record('be_http_body', bytes=len(message.get('body', b'')))
                await send(message)
            await app(scope, receive, trace_send)

        port = free_port()
        server = uvicorn.Server(uvicorn.Config(traced_app, host='127.0.0.1', port=port, log_level='warning'))
        control_thread = threading.Thread(target=control.serve_forever, daemon=True)
        control_thread.start()
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.monotonic() + 10
        while not server.started:
            if time.monotonic() > deadline:
                raise RuntimeError('Test backend did not start')
            time.sleep(0.05)
        env = {**os.environ, 'VITE_API_BASE_URL': f'http://127.0.0.1:{port}'}
        result = subprocess.run(['node', 'scripts/verify-sse.mjs', '--backend', f'http://127.0.0.1:{port}',
                                 '--probe', probe_url, '--mode', args.mode], cwd=frontend, env=env, timeout=240)
        events = sorted(state['events'], key=lambda item: item['at_ms'])
        controlled = {item['event']: item for item in events if item['mode'] == 'controlled'}
        causal_order = all(name in controlled for name in (
            'controlled_write_A', 'fe_observed_A', 'release_B', 'controlled_write_B', 'fe_complete'
        )) and (controlled['controlled_write_A']['at_ms'] <= controlled['fe_observed_A']['at_ms']
                <= controlled['release_B']['at_ms'] <= controlled['controlled_write_B']['at_ms']
                and any(item['mode'] == 'controlled' and item['result'] == 'PASS' for item in state['results']))
        if args.mode == 'live':
            causal_order = None
        negative_pass = (result.returncode != 0 and 'gate_timeout_no_FE_answer' in controlled) if args.negative_control else None
        report = {'backend_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                  'frontend_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=frontend, text=True).strip(),
                  'mode': args.mode, 'negative_control': args.negative_control,
                  'model': os.environ.get('AI_MODEL', 'gemini-3.8-flash-high'),
                  'upstream_timeout_seconds': os.environ.get('AI_TIMEOUT_SECONDS', '120'),
                  'controlled_causal_order_pass': causal_order, 'negative_control_pass': negative_pass,
                  'results': state['results'], 'events': events}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        print(json.dumps({'controlled_causal_order_pass': causal_order, 'negative_control_pass': negative_pass,
                          'evidence': str(args.output)}, ensure_ascii=False), flush=True)
        if args.negative_control:
            return 0 if negative_pass else 1
        return result.returncode or (1 if causal_order is False else 0)
    finally:
        gate.set()
        if server is not None:
            server.should_exit = True
            if thread is not None:
                thread.join(timeout=10)
        if control_thread is not None:
            control.shutdown()
        control.server_close()
        if 'engine' in locals():
            engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
        print('Temporary PostgreSQL schema and probe servers removed', flush=True)


if __name__ == '__main__':
    raise SystemExit(main())
