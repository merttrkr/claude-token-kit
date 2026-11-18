import sqlite3
from pathlib import Path

import pytest
from conftest import PROJECT, S1, S2, SONNET, assistant, write_jsonl

from tokenlab.ingest import ingest, text_len, tool_target


@pytest.fixture
def conn(mini_corpus: Path, tmp_path: Path) -> sqlite3.Connection:
    db = tmp_path / "ledger.sqlite"
    ingest(mini_corpus, db)
    return sqlite3.connect(db)


def test_requests_golden(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT thread_id, request_id, seq, model, effort, input, cache_read,"
        " cache_write, cache_write_5m, cache_write_1h, output, thinking, ttl_class,"
        " ROUND(cost_usd, 9) FROM requests ORDER BY thread_id, seq"
    ).fetchall()
    assert rows == [
        (
            S1,
            "req_1",
            0,
            "claude-opus-5-5",
            "high",
            3,
            0,
            1000,
            0,
            1000,
            26,
            12,
            "1h",
            0.008532,
        ),
        (
            S1,
            "req_2",
            1,
            "claude-opus-5-5",
            "high",
            2,
            1000,
            200,
            0,
            200,
            40,
            0,
            "1h",
            0.002608,
        ),
        (
            S2,
            "req_4",
            0,
            "claude-opus-5-5",
            "high",
            0,
            0,
            0,
            0,
            0,
            100,
            0,
            "none",
            0.002,
        ),
        (
            "agent-a1",
            "req_3",
            0,
            "claude-sonnet-5-5",
            "high",
            5,
            0,
            300,
            300,
            0,
            50,
            0,
            "5m",
            0.00126,
        ),
    ]


def test_streamed_lines_keep_the_max_usage_not_the_first(
    conn: sqlite3.Connection,
) -> None:
    # Taking the first streamed line undercounted output 2.6x on the real corpus.
    (output,) = conn.execute(
        "SELECT output FROM requests WHERE request_id='req_1'"
    ).fetchone()
    assert output == 26


def test_synthetic_messages_and_truncated_lines_are_skipped(
    conn: sqlite3.Connection,
) -> None:
    (n,) = conn.execute(
        "SELECT COUNT(*) FROM requests WHERE thread_id=?", (S1,)
    ).fetchone()
    assert n == 2


def test_threads_carry_subagent_metadata(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT thread_id, session_id, kind, agent_type, description, spawn_depth,"
        " tool_use_id, file FROM threads ORDER BY thread_id"
    ).fetchall()
    assert rows == [
        (
            S1,
            S1,
            "main",
            None,
            None,
            None,
            None,
            f"-Users-test-Development-proj/{S1}.jsonl",
        ),
        (
            S2,
            S2,
            "main",
            None,
            None,
            None,
            None,
            f"-Users-test-Development-proj/{S2}.jsonl",
        ),
        (
            "agent-a1",
            S1,
            "subagent",
            "test-writer",
            "Write tests",
            1,
            "tu0",
            f"-Users-test-Development-proj/{S1}/subagents/agent-a1.jsonl",
        ),
    ]


def test_agent_continued_in_a_resumed_session_is_its_own_thread(
    mini_corpus: Path, tmp_path: Path
) -> None:
    write_jsonl(
        mini_corpus / PROJECT / S2 / "subagents" / "agent-a1.jsonl",
        [assistant(25, "req_9", SONNET, content=[], output=7)],
    )
    db = tmp_path / "ledger.sqlite"
    ingest(mini_corpus, db)
    rows = (
        sqlite3.connect(db)
        .execute(
            "SELECT r.thread_id, r.session_id, t.session_id, r.request_id"
            " FROM requests r JOIN threads t USING (thread_id)"
            " WHERE r.thread_id LIKE 'agent-a1%' ORDER BY r.thread_id"
        )
        .fetchall()
    )
    assert rows == [
        ("agent-a1", S1, S1, "req_3"),
        (f"agent-a1@{S2}", S2, S2, "req_9"),
    ]


def test_content_links_results_to_their_call(conn: sqlite3.Connection) -> None:
    rows = conn.execute(
        "SELECT request_seq, kind, tool_use_id, tool, target, chars"
        " FROM content ORDER BY kind"
    ).fetchall()
    assert rows == [
        (0, "tool_result", "tu1", "Read", "/x/a.py", 600),
        (0, "tool_use", "tu1", "Read", "/x/a.py", len('{"file_path": "/x/a.py"}')),
    ]


def test_prefix_counts_rendered_attachments_and_each_shape_once(
    conn: sqlite3.Connection,
) -> None:
    rows = conn.execute(
        "SELECT request_seq, source, name, chars, shape_hash FROM prefix"
        " ORDER BY source, name"
    ).fetchall()
    assert rows == [
        (-1, "attachment", "skill_listing", 120, None),
        (0, "system", "system[0]", 50, "h1"),
        (0, "system", "system[1]", 30, "h1"),
        (0, "tool", "Read", len('{"name": "Read", "description": "dddddddddd"}'), "h1"),
    ]


def test_cost_state_and_events(conn: sqlite3.Connection) -> None:
    assert conn.execute(
        "SELECT session_id, model, output, asof FROM cost_state"
        " ORDER BY session_id, model"
    ).fetchall() == [
        (S1, "claude-haiku-5-5", 20, "2026-10-01T10:00:11.000Z"),
        (S1, "claude-opus-5-5", 66, "2026-10-01T10:00:11.000Z"),
        (S1, "claude-sonnet-5-5", 50, "2026-10-01T10:00:11.000Z"),
        (S2, "claude-opus-5-5", 130, "2026-10-01T10:00:30.000Z"),
    ]
    assert conn.execute(
        "SELECT session_id, subtype, trigger, pre_tokens, post_tokens FROM events"
    ).fetchall() == [(S2, "compact_boundary", "auto", 5000, 25)]


def test_ingest_is_deterministic(mini_corpus: Path, tmp_path: Path) -> None:
    assert ingest(mini_corpus, tmp_path / "a.sqlite") == ingest(
        mini_corpus, tmp_path / "b.sqlite"
    )


def test_text_len_counts_only_text() -> None:
    blocks = [
        {"type": "text", "text": "abc"},
        {"type": "image", "source": {"data": "x" * 999}},
        "de",
    ]
    assert text_len(blocks) == 5


def test_tool_target_prefers_the_acted_on_path() -> None:
    assert tool_target("Bash", {"command": "ls", "description": "list"}) == "ls"
    assert tool_target("SendMessage", {"message": "hi"}) is None
