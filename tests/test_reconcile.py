import json
import sqlite3
from pathlib import Path

import pytest
from conftest import S1, S2

from tokenlab import reconcile
from tokenlab.ingest import ingest


@pytest.fixture
def rows(mini_corpus: Path, tmp_path: Path) -> list[reconcile.Row]:
    db = tmp_path / "ledger.sqlite"
    ingest(mini_corpus, db)
    return reconcile.rows(sqlite3.connect(db))


def test_each_row_is_classified_by_source(rows: list[reconcile.Row]) -> None:
    assert [(r.session_id, r.model, r.source) for r in rows] == [
        (S1, "claude-haiku-5-5", "background"),
        (S1, "claude-opus-5-5", "matched"),
        (S1, "claude-sonnet-5-5", "matched"),
        (S2, "claude-opus-5-5", "compaction"),
    ]


def test_matched_rows_reconcile_exactly_including_subagents(
    rows: list[reconcile.Row],
) -> None:
    for r in rows:
        if r.source == "matched":
            assert r.ledger == r.truth
            assert r.ledger_cost == pytest.approx(r.truth_cost)


def test_gate_ignores_known_invisible_sources(rows: list[reconcile.Row]) -> None:
    assert reconcile.output_reconciles(rows)
    report = reconcile.render(rows)
    assert "compaction: 1 rows" in report
    assert "background: 1 rows" in report
    assert report.endswith("on matched rows: PASS\n")


def test_gate_fails_when_matched_output_is_missing(rows: list[reconcile.Row]) -> None:
    short = [
        reconcile.Row(
            **{**r.__dict__, "ledger": {**r.ledger, "output": r.ledger["output"] // 2}}
        )
        if r.source == "matched"
        else r
        for r in rows
    ]
    assert not reconcile.output_reconciles(short)


def test_requests_after_the_snapshot_are_not_counted(
    mini_corpus: Path, tmp_path: Path
) -> None:
    session = mini_corpus / "-Users-test-Development-proj" / f"{S1}.jsonl"
    late = (
        json.dumps(
            {
                "type": "assistant",
                "timestamp": "2026-10-01T10:05:00.000Z",
                "requestId": "req_late",
                "message": {
                    "model": "claude-opus-5-5",
                    "usage": {"output_tokens": 999},
                },
            },
            separators=(",", ":"),
        )
        + "\n"
    )
    session.write_text(session.read_text().rsplit("\n", 1)[0] + "\n" + late)
    db = tmp_path / "late.sqlite"
    ingest(mini_corpus, db)
    opus = next(
        r
        for r in reconcile.rows(sqlite3.connect(db))
        if r.session_id == S1 and r.model == "claude-opus-5-5"
    )
    assert opus.ledger["output"] == 66
