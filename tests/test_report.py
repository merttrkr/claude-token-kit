import sqlite3
from pathlib import Path

import pytest
from test_otel import S1_EVENTS, build, export

from tokenlab import report

GOLDEN = Path(__file__).parent / "golden" / "report_mini.txt"


@pytest.fixture
def conn(mini_corpus: Path, tmp_path: Path) -> sqlite3.Connection:
    return build(mini_corpus, tmp_path, export(*S1_EVENTS))


def test_otel_cost_is_attributed_to_threads_and_hidden_sources(
    conn: sqlite3.Connection,
) -> None:
    assert report.by_thread(conn) == [
        ("main", 2, 66, 1000, 1200, pytest.approx(0.01114)),
        ("test-writer", 1, 50, 0, 300, pytest.approx(0.00126)),
        ("hidden: generate_session_title", 1, 20, 0, 0, pytest.approx(0.0001)),
    ]


def test_thinking_is_split_from_output_where_the_transcript_has_it(
    conn: sqlite3.Connection,
) -> None:
    rows = {name: tokens for name, tokens, _ in report.by_mechanism(conn)}
    assert rows["output: thinking"] == 12
    assert rows["output: visible"] == 66 - 12 + 50
    assert rows["output: hidden requests"] == 20


def test_reads_and_char_turns(conn: sqlite3.Connection) -> None:
    assert report.reread_files(conn) == [("/x/a.py", 1, 600, 1)]
    # The Read result stays in context for the one request after it.
    assert report.char_turns(conn) == [("main", "Read", "/x/a.py", 600, 1, 600)]


@pytest.mark.parametrize(
    ("gap_s", "model_switch", "compacted", "cause"),
    [
        (4000, False, False, "idle > 1h"),
        (400, False, False, "idle > 5m"),
        (10, False, False, "prefix change"),
        (4000, True, False, "model switch"),
        (4000, True, True, "compaction"),
    ],
)
def test_rewrite_cause(
    gap_s: float, model_switch: bool, compacted: bool, cause: str
) -> None:
    assert report.rewrite_cause(gap_s, model_switch, compacted) == cause


def test_report_is_byte_identical_and_matches_golden(
    conn: sqlite3.Connection,
) -> None:
    text = report.render(conn)
    assert report.render(conn) == text
    assert text == GOLDEN.read_text(encoding="utf-8")


def test_no_otel_means_no_report(mini_corpus: Path, tmp_path: Path) -> None:
    from tokenlab.ingest import ingest

    db = tmp_path / "ledger.sqlite"
    ingest(mini_corpus, db)
    assert report.render(sqlite3.connect(db)) == "no OTel requests in the ledger\n"
