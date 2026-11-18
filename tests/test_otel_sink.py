import io
import json
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

from tokenlab.otel_sink import make_handler, record

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def test_record_keeps_json_bodies_structured() -> None:
    line = record("/v1/logs", b'{"resourceLogs": []}', NOW)
    assert json.loads(line) == {
        "body": {"resourceLogs": []},
        "path": "/v1/logs",
        "received_at": "2026-10-30T12:00:00+00:00",
    }


def test_record_keeps_non_json_bodies_as_text() -> None:
    assert json.loads(record("/v1/logs", b"\xffnot json", NOW))["body"] == "�not json"


def _post(out: Path, path: str, body: bytes) -> bytes:
    handler_cls = make_handler(out)
    h = handler_cls.__new__(handler_cls)
    h.path = path
    headers = Message()
    headers["Content-Length"] = str(len(body))
    h.headers = headers  # type: ignore[assignment]
    h.rfile = io.BytesIO(body)
    h.wfile = io.BytesIO()
    h.request_version = "HTTP/1.1"
    h.requestline = f"POST {path} HTTP/1.1"
    h.command = "POST"
    h.do_POST()
    return h.wfile.getvalue()


def test_handler_appends_one_line_per_export(tmp_path: Path) -> None:
    out = tmp_path / "otel.jsonl"
    _post(out, "/v1/logs", b'{"a": 1}')
    _post(out, "/v1/metrics?x=1", b'{"b": 2}')
    lines = [json.loads(x) for x in out.read_text().splitlines()]
    assert [(x["path"], x["body"]) for x in lines] == [
        ("/v1/logs", {"a": 1}),
        ("/v1/metrics", {"b": 2}),
    ]


def test_handler_rejects_other_paths(tmp_path: Path) -> None:
    out = tmp_path / "otel.jsonl"
    assert b"404" in _post(out, "/other", b"{}")
    assert not out.exists()
