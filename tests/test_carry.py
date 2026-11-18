"""How many requests carry a part: attachments until the next compaction, tool
definitions until the next request shape (including a return to an earlier one)."""

import sqlite3
from pathlib import Path
from typing import Any

from conftest import PROJECT, SONNET, assistant, write_jsonl

from tokenlab import why
from tokenlab.ingest import ingest

S = "44440000-0000-0000-0000-000000000004"


def at(rec: dict[str, Any], minute: int) -> dict[str, Any]:
    return {**rec, "timestamp": f"2026-10-01T10:{minute:02d}:00.000Z"}


def req(n: int, minute: int) -> dict[str, Any]:
    return at(assistant(0, f"req_{n}", SONNET, content=[], input_tokens=10), minute)


def shape(h: str, tool: str) -> dict[str, Any]:
    return {
        "type": "api-request-shape",
        "shapeHash": h,
        "shape": {"system": [], "tools": [{"name": tool, "description": "d"}]},
    }


def file_att(path: str) -> dict[str, Any]:
    return {
        "type": "attachment",
        "attachment": {
            "type": "instructions",
            "files": [{"path": path, "type": "User", "content": "c" * 10}],
        },
        "rendered": [{"content": "r"}],
    }


def test_carry_respects_compaction_and_shape_returns(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{S}.jsonl",
        [
            at(file_att("/before.md"), 0),
            at(shape("h1", "A"), 0),
            req(0, 1),
            at(shape("h2", "B"), 1),
            req(1, 2),
            at(
                {
                    "type": "system",
                    "subtype": "compact_boundary",
                    "compactMetadata": {"trigger": "auto", "preTokens": 9},
                },
                3,
            ),
            # Re-sent after the compaction, before the next request.
            at(file_att("/after.md"), 3),
            at(shape("h1", "A"), 3),
            req(2, 4),
            req(3, 5),
        ],
    )
    db = tmp_path / "l.sqlite"
    ingest(root, db)
    conn = sqlite3.connect(db)
    carry = why.Carry(conn, "")
    rows = {
        name: (seq, ts)
        for name, seq, ts in conn.execute("SELECT name, request_seq, ts FROM parts")
    }
    assert carry.after(S, *rows["/before.md"]) == 2  # req_0, req_1; then compaction
    assert carry.after(S, *rows["/after.md"]) == 2  # req_2, req_3
    shapes = [
        r
        for (r,) in conn.execute(
            "SELECT request_seq FROM prefix WHERE source = 'tool' ORDER BY request_seq"
        )
    ]
    assert shapes == [0, 1, 2]  # h1, h2, then h1 again
    assert carry.shape(S, 0) == 1 and carry.shape(S, 2) == 2
