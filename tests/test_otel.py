import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import HAIKU, OPUS, S1, S2, SONNET, START, ts

from tokenlab import reconcile
from tokenlab.ingest import ingest
from tokenlab.otel import OTEL_DIR
from tokenlab.snapshot import MANIFEST, snapshot


def event(
    second: int,
    seq: int,
    model: str,
    source: str,
    request_id: str,
    usage: tuple[int, int, int, int],
    cost: float,
    session: str = S1,
) -> dict[str, Any]:
    inp, out, read, write = usage
    attrs: dict[str, Any] = {
        "session.id": {"stringValue": session},
        "event.name": {"stringValue": "api_request"},
        "event.timestamp": {"stringValue": ts(second)},
        "event.sequence": {"intValue": seq},
        "model": {"stringValue": model},
        "query_source": {"stringValue": source},
        "request_id": {"stringValue": request_id},
        "input_tokens": {"intValue": inp},
        "output_tokens": {"intValue": out},
        "cache_read_tokens": {"intValue": read},
        "cache_creation_tokens": {"intValue": write},
        "cache_creation_5m_tokens": {"intValue": 0},
        "cache_creation_1h_tokens": {"intValue": write},
        "cost_usd": {"doubleValue": cost},
        "duration_ms": {"intValue": 900},
    }
    return {"attributes": [{"key": k, "value": v} for k, v in attrs.items()]}


def export(*records: dict[str, Any]) -> str:
    body = {
        "resourceLogs": [
            {
                "resource": {
                    "attributes": [
                        {"key": "service.version", "value": {"stringValue": "2.1.318"}}
                    ]
                },
                "scopeLogs": [{"logRecords": list(records)}],
            }
        ]
    }
    return json.dumps({"path": "/v1/logs", "body": body}) + "\n"


# S1's cost-state, request by request. The Haiku title request is in no transcript.
S1_EVENTS = [
    event(3, 1, OPUS, "repl_main_thread", "req_1", (3, 26, 0, 1000), 0.006),
    event(5, 2, SONNET, "agent:custom", "req_3", (5, 50, 0, 300), 0.00126),
    event(8, 3, HAIKU, "generate_session_title", "req_t", (900, 20, 0, 0), 0.0001),
    event(10, 4, OPUS, "repl_main_thread", "req_2", (2, 40, 1000, 200), 0.00514),
]


def build(corpus: Path, tmp_path: Path, text: str) -> sqlite3.Connection:
    (corpus / OTEL_DIR).mkdir()
    (corpus / OTEL_DIR / "otel.jsonl").write_text(text)
    db = tmp_path / "ledger.sqlite"
    ingest(corpus, db)
    return sqlite3.connect(db)


def test_otel_equals_cost_state_and_names_the_hidden_request(
    mini_corpus: Path, tmp_path: Path
) -> None:
    conn = build(mini_corpus, tmp_path, export(*S1_EVENTS[:2]) + export(*S1_EVENTS[2:]))
    rs = reconcile.otel_rows(conn)
    assert {(r.session_id, r.model) for r in rs} == {
        (S1, OPUS),
        (S1, SONNET),
        (S1, HAIKU),
    }
    assert all(r.otel == r.truth for r in rs)
    assert reconcile.otel_reconciles(rs) is True
    assert reconcile.hidden(conn) == [
        ("generate_session_title", 1, 900, 20, 0, 0, pytest.approx(0.0001))
    ]
    assert reconcile.twins(conn) == (3, 3)
    assert reconcile.render_otel(conn).endswith("of cost-state: PASS\n")


def test_retried_exports_and_torn_lines_count_once(
    mini_corpus: Path, tmp_path: Path
) -> None:
    whole = export(*S1_EVENTS)
    conn = build(mini_corpus, tmp_path, whole + whole + whole[:40])
    (n,) = conn.execute("SELECT COUNT(*) FROM otel_requests").fetchone()
    assert n == 4
    assert reconcile.otel_reconciles(reconcile.otel_rows(conn)) is True


def test_a_missing_hidden_event_fails_the_gate(
    mini_corpus: Path, tmp_path: Path
) -> None:
    title = S1_EVENTS[2]
    conn = build(mini_corpus, tmp_path, export(*(e for e in S1_EVENTS if e != title)))
    assert reconcile.otel_reconciles(reconcile.otel_rows(conn)) is False
    assert "of cost-state: FAIL" in reconcile.render_otel(conn)


def test_a_partly_covered_process_is_left_out_and_named(
    mini_corpus: Path, tmp_path: Path
) -> None:
    conn = build(mini_corpus, tmp_path, export(*S1_EVENTS[:3]))
    assert reconcile.otel_rows(conn) == []
    assert reconcile.otel_reconciles(reconcile.otel_rows(conn)) is None
    assert reconcile.partial(conn) == [(S1, START, 1)]
    assert f"{S1[:8]} {START}  transcript requests without OTel: 1" in (
        reconcile.render_otel(conn)
    )


def test_processes_without_otel_are_left_out(mini_corpus: Path, tmp_path: Path) -> None:
    conn = build(mini_corpus, tmp_path, export(*S1_EVENTS))
    assert S2 not in {r.session_id for r in reconcile.otel_rows(conn)}


def test_no_otel_means_no_verdict(mini_corpus: Path, tmp_path: Path) -> None:
    db = tmp_path / "ledger.sqlite"
    ingest(mini_corpus, db)
    conn = sqlite3.connect(db)
    assert reconcile.otel_reconciles(reconcile.otel_rows(conn)) is None
    assert reconcile.render_otel(conn) == ""


def test_otel_is_part_of_the_digest(mini_corpus: Path, tmp_path: Path) -> None:
    before = ingest(mini_corpus, tmp_path / "a.sqlite")
    (mini_corpus / OTEL_DIR).mkdir()
    (mini_corpus / OTEL_DIR / "otel.jsonl").write_text(export(*S1_EVENTS))
    after = ingest(mini_corpus, tmp_path / "b.sqlite")
    assert after != before
    assert ingest(mini_corpus, tmp_path / "c.sqlite") == after


def test_snapshot_freezes_projects_and_otel(mini_corpus: Path, tmp_path: Path) -> None:
    otel_dir = tmp_path / "otel"
    otel_dir.mkdir()
    (otel_dir / "otel.jsonl").write_text(export(*S1_EVENTS))
    dest = tmp_path / "corpus-new"
    n = snapshot(mini_corpus, otel_dir, dest)
    manifest = (dest / MANIFEST).read_text().splitlines()
    assert len(manifest) == n
    assert any(line.endswith(f"./{OTEL_DIR}/otel.jsonl") for line in manifest)
    ingest(dest, tmp_path / "s.sqlite")
    conn = sqlite3.connect(tmp_path / "s.sqlite")
    assert reconcile.otel_reconciles(reconcile.otel_rows(conn)) is True
    with pytest.raises(FileExistsError):
        snapshot(mini_corpus, otel_dir, dest)
