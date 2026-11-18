"""A local OTLP/HTTP-JSON receiver that appends every export to a JSONL file.

Claude Code emits one `api_request` log event per model request (model, usage, cost,
requestId, querySource), including the requests transcripts never show. Settings to
point it here, with prompts and responses left redacted:

    CLAUDE_CODE_ENABLE_TELEMETRY=1
    OTEL_LOGS_EXPORTER=otlp
    OTEL_EXPORTER_OTLP_PROTOCOL=http/json
    OTEL_EXPORTER_OTLP_ENDPOINT=http://127.0.0.1:4318
"""

import json
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

OTLP_PATHS = frozenset({"/v1/logs", "/v1/metrics", "/v1/traces"})


def record(path: str, body: bytes, received_at: datetime) -> str:
    """One JSONL line per export. Bodies that are not JSON are kept as text."""
    try:
        payload: object = json.loads(body)
    except ValueError:
        payload = body.decode("utf-8", "replace")
    line = {"received_at": received_at.isoformat(), "path": path, "body": payload}
    return json.dumps(line, ensure_ascii=False, sort_keys=True) + "\n"


def make_handler(out: Path) -> type[BaseHTTPRequestHandler]:
    lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 (http.server naming)
            path = self.path.split("?", 1)[0]
            if path not in OTLP_PATHS:
                self.send_error(404)
                return
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            with lock, out.open("a", encoding="utf-8") as fh:
                fh.write(record(path, body, datetime.now(UTC)))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Handler


def serve(out: Path, host: str = "127.0.0.1", port: int = 4318) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), make_handler(out))
    print(f"otel sink on http://{host}:{port} -> {out}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
