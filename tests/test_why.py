import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import PROJECT, SONNET, assistant, write_jsonl

from tokenlab import why
from tokenlab.ingest import ingest
from tokenlab.ledger import create
from tokenlab.rules import Rules

MAIN = "22220000-0000-0000-0000-000000000002"
# Every finding shows, however small, so each rule can be seen firing.
LOW = Rules(min_weekly_tokens=0, big_result_chars=100, cold_write_tokens=10)


def at(rec: dict[str, Any], minute: int) -> dict[str, Any]:
    return {**rec, "timestamp": f"2026-10-01T10:{minute:02d}:00.000Z"}


def att(kind: str, minute: int, **body: Any) -> dict[str, Any]:
    return at(
        {
            "type": "attachment",
            "attachment": {"type": kind, **body},
            "rendered": [{"content": "r"}],
        },
        minute,
    )


def use(tid: str, name: str, **inp: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid: str, chars: int, minute: int) -> dict[str, Any]:
    block = {"type": "tool_result", "tool_use_id": tid, "content": "x" * chars}
    return at({"type": "user", "message": {"content": [block]}}, minute)


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    memory = tmp_path / "memory"
    memory.mkdir()
    index = memory / "MEMORY.md"
    index.write_text(
        "# Index\n- [Read one](read-one.md) — used\n- [Idle one](idle-one.md) — not\n"
        "- [Gone](gone.md) — deleted\n",
        encoding="utf-8",
    )
    for name in ("read-one.md", "idle-one.md"):
        (memory / name).write_text("x", encoding="utf-8")
    shape = {
        "type": "api-request-shape",
        "shapeHash": "h1",
        "shape": {
            "system": [{"type": "text", "text": "s" * 400}],
            "tools": [
                {"name": "Read", "description": "r" * 300},
                {"name": "Unused", "description": "u" * 900},
                {"name": "mcp__srv__ping", "description": "p" * 500},
            ],
        },
    }
    records: list[dict[str, Any]] = [
        att(
            "instructions",
            0,
            files=[{"path": str(index), "type": "AutoMem", "content": "i" * 600}],
        ),
        att(
            "skill_listing",
            0,
            content="- used-skill: a\n- idle-skill: b\n- plug:idle: c",
        ),
        att(
            "agent_listing_delta",
            0,
            addedTypes=["scout", "idle-agent"],
            addedLines=["- scout: finds", "- idle-agent: never"],
        ),
        at(shape, 0),
        at(
            assistant(
                0,
                "req_0",
                SONNET,
                content=[
                    use("t1", "Read", file_path=str(memory / "read-one.md")),
                    use("t2", "Skill", skill="used-skill"),
                    use("t3", "Agent", subagent_type="scout"),
                ],
                input_tokens=4000,
            ),
            1,
        ),
        result("t1", 5000, 1),
        at(assistant(0, "req_1", SONNET, content=[], read=4000), 2),
        # Twelve idle minutes: the 5-minute cache is cold and gets rewritten.
        at(assistant(0, "req_2", SONNET, content=[], w5m=3000), 14),
        at(assistant(0, "req_3", SONNET, content=[], read=7000), 15),
    ]
    root = tmp_path / "corpus"
    write_jsonl(root / PROJECT / f"{MAIN}.jsonl", records)
    sub = root / PROJECT / MAIN / "subagents"
    write_jsonl(
        sub / "agent-s1.jsonl",
        [at(assistant(0, "req_s", SONNET, content=[], input_tokens=50), 1)],
    )
    (sub / "agent-s1.meta.json").write_text(
        json.dumps({"agentType": "scout", "toolUseId": "t3"})
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    return sqlite3.connect(db)


def finding(text: str, code: str) -> list[str]:
    return [r for r in text.splitlines() if r.startswith(f"| {code} |")]


def test_attachments_ride_until_compaction_and_tools_until_the_next_shape(
    conn: sqlite3.Connection,
) -> None:
    carry = why.Carry(conn, "")
    assert carry.after(MAIN, -1, None) == 4  # all four main requests
    assert carry.after(MAIN, 1, None) == 2  # placed after req_1: req_2 and req_3
    assert carry.shape(MAIN, 0) == 4


def test_unused_tools_skills_and_agents_are_findings(conn: sqlite3.Connection) -> None:
    text = why.render(conn, 30, LOW)
    tools = " ".join(finding(text, "T"))
    assert "`Unused`" in tools and "MCP `srv`" in tools and "`Read`" not in tools
    skills = finding(text, "S")
    assert any("senin 1 skill" in r and "idle-skill" in r for r in skills)
    assert any("`plug` plugin" in r and "plug:idle" in r for r in skills)
    assert "used-skill" not in " ".join(skills)
    agents = " ".join(finding(text, "A"))
    assert "idle-agent" in agents and "scout" not in agents


def test_memory_lines_whose_file_was_never_read_or_is_gone(
    conn: sqlite3.Connection,
) -> None:
    rows = finding(why.render(conn, 30, LOW), "M")
    unread = [r for r in rows if "hiç okunmadı" in r]
    gone = [r for r in rows if "dosyası yok" in r]
    assert len(unread) == 1 and "idle-one.md" in unread[0]
    assert "read-one.md" not in " ".join(rows)
    assert len(gone) == 1 and "gone.md" in gone[0]


def test_big_results_and_cold_cache(conn: sqlite3.Connection) -> None:
    text = why.render(conn, 30, LOW)
    assert any("`Read` 1 kez" in r for r in finding(text, "R"))
    assert any("1 kez cache soğudu" in r for r in finding(text, "C"))


def test_an_item_gone_from_recent_sessions_counts_as_fixed(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    # Three days later a session lists the same skills except the idle one.
    later = "33330000-0000-0000-0000-000000000003"
    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{later}.jsonl",
        [
            {
                **att("skill_listing", 0, content="- used-skill: a\n- plug:idle: c"),
                "timestamp": "2026-10-04T10:00:00.000Z",
            },
            {
                **assistant(0, "req_9", SONNET, content=[], input_tokens=10),
                "timestamp": "2026-10-04T10:01:00.000Z",
            },
        ],
    )
    db = tmp_path / "later.sqlite"
    ingest(root, db)
    text = why.render(sqlite3.connect(db), 30, LOW)
    skills = " ".join(finding(text, "S"))
    assert "idle-skill" not in skills and "plug:idle" in skills
    assert "zaten düzelmiş görünüyor" in text


def test_a_finding_below_the_cutoff_is_not_shown(conn: sqlite3.Connection) -> None:
    high = Rules(min_weekly_tokens=10**12, big_result_chars=100, cold_write_tokens=10)
    assert "Eşiği geçen bulgu yok." in why.render(conn, 30, high)


def test_a_carried_part_is_written_once_then_read() -> None:
    assert why.weighted(100, 0) == 0
    assert why.weighted(100, 1) == 125  # one cache write
    assert why.weighted(100, 11) == pytest.approx(225)  # + ten reads at 0.1


def test_findings_are_sorted_by_weighted_cost(conn: sqlite3.Connection) -> None:
    rows = [r.split(" | ") for r in why.render(conn, 30, LOW).splitlines()]
    costs = [int(r[1].replace(",", "")) for r in rows if len(r) > 2 and len(r[0]) == 3]
    assert costs and costs == sorted(costs, reverse=True)


def test_a_finding_carries_the_formula_numbers(conn: sqlite3.Connection) -> None:
    tool = {"name": "Unused", "description": "u" * 900}
    chars = len(json.dumps(tool, ensure_ascii=False))
    n = why.Carry(conn, "").shape(MAIN, 0)
    tpc, week = why.tokens_per_char(conn) or 0.25, 7.0  # a one-day window
    row = next(r for r in finding(why.render(conn, 30, LOW), "T") if "`Unused`" in r)
    cost, raw = (int(x.replace(",", "")) for x in row.split(" | ")[1:3])
    assert n == 4
    assert cost == int(why.weighted(chars, n) * week * tpc)
    assert raw == int(chars * n * week * tpc)


def test_report_is_byte_identical_after_a_rebuild(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    again = tmp_path / "again.sqlite"
    ingest(tmp_path / "corpus", again)
    rebuilt = sqlite3.connect(again)
    assert why.render(conn, 30, LOW) == why.render(rebuilt, 30, LOW)


def test_an_empty_ledger_says_so(tmp_path: Path) -> None:
    empty = create(tmp_path / "e.sqlite")
    assert "Henüz hiç Claude Code isteği yok" in why.render(empty, 30)
