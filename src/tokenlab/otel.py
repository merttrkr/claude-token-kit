"""OTel `api_request` events, as the sink wrote them, into the ledger.

Each event is one model request Claude Code made, including the ones it never writes
to a transcript (titles, compaction, suggestions). Exporter retries can deliver the
same event twice; `(session_id, ts, seq)` identifies it.
"""

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

OTEL_DIR = "_otel"

COLUMNS = (
    "session_id",
    "ts",
    "seq",
    "version",
    "model",
    "query_source",
    "effort",
    "speed",
    "request_id",
    "prompt_id",
    "input",
    "output",
    "cache_read",
    "cache_write",
    "cache_write_5m",
    "cache_write_1h",
    "cost_usd",
    "duration_ms",
    "ttft_ms",
)

# column -> OTel attribute, where the names differ
ATTRS = {
    "session_id": "session.id",
    "ts": "event.timestamp",
    "seq": "event.sequence",
    "prompt_id": "prompt.id",
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_tokens",
    "cache_write": "cache_creation_tokens",
    "cache_write_5m": "cache_creation_5m_tokens",
    "cache_write_1h": "cache_creation_1h_tokens",
}
INTS = frozenset(
    {
        "seq",
        "input",
        "output",
        "cache_read",
        "cache_write",
        "cache_write_5m",
        "cache_write_1h",
        "duration_ms",
        "ttft_ms",
    }
)


def _value(v: dict[str, Any]) -> Any:
    for key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if key in v:
            return v[key]
    return None


def _attrs(items: list[dict[str, Any]]) -> dict[str, Any]:
    return {a["key"]: _value(a.get("value") or {}) for a in items}


def api_requests(path: Path) -> Iterator[tuple[Any, ...]]:
    """Rows in COLUMNS order. Lines that are not JSON (a copy taken mid-write) are
    skipped."""
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("path") != "/v1/logs" or not isinstance(rec.get("body"), dict):
                continue
            for rl in rec["body"].get("resourceLogs", []):
                resource = _attrs((rl.get("resource") or {}).get("attributes", []))
                for sl in rl.get("scopeLogs", []):
                    for lr in sl.get("logRecords", []):
                        a = _attrs(lr.get("attributes", []))
                        if a.get("event.name") != "api_request":
                            continue
                        a["version"] = resource.get("service.version")
                        row = []
                        for col in COLUMNS:
                            val = a.get(ATTRS.get(col, col))
                            if col in INTS:
                                val = int(val or 0)
                            elif col == "cost_usd":
                                val = float(val or 0)
                            row.append(val)
                        yield tuple(row)


def load(conn: sqlite3.Connection, root: Path) -> None:
    sql = (
        f"INSERT OR IGNORE INTO otel_requests ({', '.join(COLUMNS)})"  # noqa: S608
        f" VALUES ({', '.join('?' * len(COLUMNS))})"
    )
    for path in sorted((root / OTEL_DIR).glob("*.jsonl")):
        conn.executemany(sql, api_requests(path))
