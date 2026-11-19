import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import PROJECT, SONNET, assistant, write_jsonl

from tokenlab import agents, why
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
            {
                "type": "attachment",
                "attachment": {"type": "hook_additional_context", "content": ["h"]},
                "rendered": [{"content": "h" * 700}],
            },
            0,
        ),
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
    write_jsonl(
        sub / "agent-g1.jsonl",
        [at(assistant(0, "req_g", SONNET, content=[], input_tokens=50), 1)],
    )
    (sub / "agent-g1.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "toolUseId": "t4"})
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    return sqlite3.connect(db)


def finding(text: str, code: str) -> list[str]:
    section = text.split("## 2.", 1)[1].split("## 3.", 1)[0]
    return [r for r in section.splitlines() if r.startswith(f"| {why.ANGLE[code]} |")]


def summary(text: str) -> str:
    return text.split("## 1.", 1)[0]


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
    assert any("1 skill listed" in r and "idle-skill" in r for r in skills)
    assert any("plugin `plug`" in r and "plug:idle" in r for r in skills)
    assert "used-skill" not in " ".join(skills)
    agents = " ".join(finding(text, "A"))
    assert "idle-agent" in agents and "scout" not in agents


def test_memory_lines_whose_file_was_never_read_or_is_gone(
    conn: sqlite3.Connection,
) -> None:
    rows = finding(why.render(conn, 30, LOW), "M")
    unread = [r for r in rows if "never read" in r]
    gone = [r for r in rows if "missing files" in r]
    assert len(unread) == 1 and "idle-one.md" in unread[0]
    assert "read-one.md" not in " ".join(rows)
    assert len(gone) == 1 and "gone.md" in gone[0]


def test_big_results_and_cold_cache(conn: sqlite3.Connection) -> None:
    text = why.render(conn, 30, LOW)
    big = [r for r in finding(text, "R") if "`Read` ×1" in r]
    assert big and "read-one.md" in big[0]  # the evidence names the file
    assert "cache went cold 1 time:" in summary(text)


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
    # Five distinct items (two agents, a skill, two tools), each counted once
    # though several checks look at the same listing.
    assert "5 items are absent" in text and "they look already fixed" in text


def test_a_finding_below_the_cutoff_is_not_shown(conn: sqlite3.Connection) -> None:
    high = Rules(min_weekly_tokens=10**12, big_result_chars=100, cold_write_tokens=10)
    assert "No finding above the threshold." in why.render(conn, 30, high)


def test_a_carried_part_is_written_once_then_read() -> None:
    assert why.weighted(100, 0) == 0
    assert why.weighted(100, 1) == 125  # one cache write
    assert why.weighted(100, 11) == pytest.approx(225)  # + ten reads at 0.1


def test_findings_are_sorted_by_weighted_cost(conn: sqlite3.Connection) -> None:
    rows = [r.split(" | ") for r in why.render(conn, 30, LOW).splitlines()]
    angles = tuple(f"| {a}" for a in why.ANGLE.values())
    section = why.render(conn, 30, LOW).split("## 2.", 1)[1].split("## 3.", 1)[0]
    rows = [r.split(" | ") for r in section.splitlines() if r.startswith(angles)]
    costs = [int(r[2].replace(",", "")) for r in rows]
    assert costs and costs == sorted(costs, reverse=True)


def test_a_finding_carries_the_formula_numbers(conn: sqlite3.Connection) -> None:
    tool = {"name": "Unused", "description": "u" * 900}
    chars = len(json.dumps(tool, ensure_ascii=False))
    n = why.Carry(conn, "").shape(MAIN, 0)
    tpc, week = why.tokens_per_char(conn) or 0.25, 7.0  # a one-day window
    row = next(r for r in finding(why.render(conn, 30, LOW), "T") if "`Unused`" in r)
    cost = int(row.split(" | ")[2].replace(",", ""))
    assert n == 4
    assert cost == int(why.weighted(chars, n) * week * tpc)


def test_report_is_byte_identical_after_a_rebuild(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    again = tmp_path / "again.sqlite"
    ingest(tmp_path / "corpus", again)
    rebuilt = sqlite3.connect(again)
    assert why.render(conn, 30, LOW) == why.render(rebuilt, 30, LOW)


def test_an_empty_ledger_says_so(tmp_path: Path) -> None:
    empty = create(tmp_path / "e.sqlite")
    assert "No Claude Code requests yet" in why.render(empty, 30)


def test_the_report_opens_with_a_summary_of_every_angle(
    conn: sqlite3.Connection,
) -> None:
    head = summary(why.render(conn, 30, LOW))
    assert head.index("## Summary") < head.index("## First step")
    # One line per angle, findings or not, so nothing is silently skipped.
    for angle in why.ANGLE.values():
        assert f"| {angle} |" in head
    assert "cuttable" in head.lower()


def test_cache_cooling_is_an_observation_not_the_first_step(
    conn: sqlite3.Connection,
) -> None:
    text = why.render(conn, 30, LOW)
    first = text.split("## First step", 1)[1].split("##", 1)[0]
    assert "cache" not in first.lower()
    cache = next(r for r in summary(text).splitlines() if r.startswith("| Cache |"))
    assert "observation" in cache


def test_agents_line_gives_the_general_purpose_share(conn: sqlite3.Connection) -> None:
    head = summary(why.render(conn, 30, LOW))
    agents = next(r for r in head.splitlines() if r.startswith("| Agents |"))
    assert "general-purpose share 50%" in agents  # 1 of 2 subagent calls


def test_hook_text_added_to_the_context_is_measured(conn: sqlite3.Connection) -> None:
    head = summary(why.render(conn, 30, LOW))
    hooks = next(r for r in head.splitlines() if r.startswith("| Hooks |"))
    assert "hook text" in hooks


def test_hook_text_is_part_of_the_repeated_part(conn: sqlite3.Connection) -> None:
    section = why.render(conn, 30, LOW).split("## 1.", 1)[1].split("## 2.", 1)[0]
    assert "| hook text |" in section


def test_a_big_read_read_again_is_counted_once(tmp_path: Path) -> None:
    # The same file read three times, each result big: one finding, not two.
    root = tmp_path / "corpus"
    records: list[dict[str, Any]] = []
    for i in range(3):
        records += [
            at(
                assistant(
                    i,
                    f"req_{i}",
                    SONNET,
                    content=[use(f"t{i}", "Read", file_path="/w/big.md")],
                    input_tokens=10,
                ),
                i * 2,
            ),
            result(f"t{i}", 500, i * 2 + 1),
        ]
    records.append(at(assistant(3, "req_3", SONNET, content=[], input_tokens=10), 7))
    write_jsonl(root / PROJECT / f"{MAIN}.jsonl", records)
    db = tmp_path / "big.sqlite"
    ingest(root, db)
    rows = finding(why.render(sqlite3.connect(db), 30, LOW), "R")
    assert any("`Read` ×3" in r for r in rows)
    assert not any("times in one session" in r for r in rows)


def test_agent_trend_shows_the_last_week_and_the_last_days(tmp_path: Path) -> None:
    # general-purpose on day 1 and day 10, a named agent on day 15.
    root = tmp_path / "corpus"
    day = "2026-10-{:02d}T10:00:00.000Z"
    write_jsonl(
        root / PROJECT / f"{MAIN}.jsonl",
        [
            {
                **assistant(i, f"req_{i}", SONNET, content=[], input_tokens=10),
                "timestamp": day.format(d),
            }
            for i, d in enumerate((1, 10, 15))
        ],
    )
    sub = root / PROJECT / MAIN / "subagents"
    for name, kind, d in (
        ("a1", "general-purpose", 1),
        ("a2", "general-purpose", 10),
        ("a3", "scout", 15),
    ):
        write_jsonl(
            sub / f"agent-{name}.jsonl",
            [
                {
                    **assistant(0, f"req_{name}", SONNET, content=[], input_tokens=5),
                    "timestamp": day.format(d),
                }
            ],
        )
        (sub / f"agent-{name}.meta.json").write_text(json.dumps({"agentType": kind}))
    db = tmp_path / "trend.sqlite"
    ingest(root, db)
    head = summary(why.render(sqlite3.connect(db), 30, LOW))
    agents = next(r for r in head.splitlines() if r.startswith("| Agents |"))
    assert "last 7 days 50%, last 2 days 0%" in agents
    # The latest share is 0%: no advice to move work to a named agent.
    assert "a named agent" not in agents


def three_days(
    tmp_path: Path,
    main_chars: int = 0,
    gp_chars: int = 0,
    per_turn: int = 1,
    gp_tool: str = "Read",
    catalog: dict[str, agents.AgentDef] | None = None,
    main_tool: str = "mcp__tracker__get_issue",
    rules: Rules = LOW,
) -> str:
    """Three sessions on three days. Each has two user messages; each message
    reads the tracker per_turn times in main (main_chars per result) and runs a
    general-purpose agent that Reads a spec (gp_chars). Returns the report."""
    root = tmp_path / "corpus"
    for d in (1, 2, 3):
        sid = f"4444000{d}-0000-0000-0000-000000000000"
        stamp = f"2026-10-0{d}T10:%02d:00.000Z"
        records: list[dict[str, Any]] = []
        for i in range(2):
            calls = [use(f"t{d}{i}{k}", main_tool, key="X-1") for k in range(per_turn)]
            if gp_chars:
                calls.append(use(f"a{d}{i}", "Agent", subagent_type="general-purpose"))
            records.append(
                {
                    "type": "user",
                    "message": {"content": "look"},
                    "timestamp": stamp % (i * 2),
                }
            )
            records.append(
                {
                    **assistant(
                        i, f"req_{d}{i}", SONNET, content=calls, input_tokens=10
                    ),
                    "timestamp": stamp % (i * 2),
                }
            )
            if main_chars:
                records += [
                    {
                        **result(f"t{d}{i}{k}", main_chars, 0),
                        "timestamp": stamp % (i * 2 + 1),
                    }
                    for k in range(per_turn)
                ]
        records.append(
            {
                **assistant(2, f"req_{d}9", SONNET, content=[], input_tokens=10),
                "timestamp": stamp % 9,
            }
        )
        write_jsonl(root / PROJECT / f"{sid}.jsonl", records)
        if not gp_chars:
            continue
        sub = root / PROJECT / sid / "subagents"
        for i in range(2):
            name = f"agent-g{d}{i}"
            write_jsonl(
                sub / f"{name}.jsonl",
                [
                    {
                        **assistant(
                            0,
                            f"req_g{d}{i}0",
                            SONNET,
                            content=[
                                use(f"r{d}{i}", gp_tool, file_path="/w/specs/a.md")
                            ],
                            input_tokens=10,
                        ),
                        "timestamp": stamp % 1,
                    },
                    {**result(f"r{d}{i}", gp_chars, 0), "timestamp": stamp % 2},
                    {
                        **assistant(
                            1, f"req_g{d}{i}1", SONNET, content=[], input_tokens=10
                        ),
                        "timestamp": stamp % 3,
                    },
                ],
            )
            (sub / f"{name}.meta.json").write_text(
                json.dumps({"agentType": "general-purpose", "toolUseId": f"a{d}{i}"})
            )
    db = tmp_path / "three.sqlite"
    ingest(root, db)
    return why.render(sqlite3.connect(db), 30, rules, catalog)


def candidates_table(text: str) -> list[str]:
    if "### Agent candidates" not in text:
        return []
    part = text.split("### Agent candidates", 1)[1].split("##", 1)[0]
    return [r for r in part.splitlines() if r.startswith("| ") and "---" not in r][1:]


def test_main_session_reads_that_pass_the_standard_are_an_agent_candidate(
    tmp_path: Path,
) -> None:
    text = three_days(tmp_path, main_chars=6000)
    found = [r for r in finding(text, "A") if "`tracker`" in r]
    assert found and "6 times" in found[0] and "agent" in found[0]
    # The same results are not counted again as big tool output.
    assert not any("get_issue" in r for r in finding(text, "R"))
    rows = [r for r in candidates_table(text) if "tracker" in r]
    assert rows and "main session" in rows[0] and "agent candidate" in rows[0]


def test_main_session_reads_smaller_than_a_summary_are_not(tmp_path: Path) -> None:
    text = three_days(tmp_path, main_chars=500)
    assert not any("`tracker`" in r for r in finding(text, "A"))
    rows = [r for r in candidates_table(text) if "tracker" in r]
    assert rows and "not worth it" in rows[0]


def test_a_recurring_general_purpose_job_is_measured_against_the_standard(
    tmp_path: Path,
) -> None:
    rows = [
        r
        for r in candidates_table(three_days(tmp_path, gp_chars=6000))
        if "general-purpose" in r
    ]
    assert rows and "agent candidate" in rows[0] and "| 6 |" in rows[0]


def test_the_agents_line_counts_jobs_that_pass_the_standard(tmp_path: Path) -> None:
    head = summary(three_days(tmp_path, main_chars=6000, gp_chars=6000))
    agents = next(r for r in head.splitlines() if r.startswith("| Agents |"))
    assert "2 jobs meet the measured agent conditions" in agents
    assert "write the agent candidates" in agents


def test_main_session_reads_are_measured_per_user_message(tmp_path: Path) -> None:
    # Each read is small, but one message's three reads add up to more than a
    # summary: an agent doing that message's reading would pay off.
    text = three_days(tmp_path, main_chars=2000, per_turn=3)
    rows = [r for r in candidates_table(text) if "tracker" in r]
    assert rows and "agent candidate" in rows[0] and "| 6 |" in rows[0]


def test_work_with_no_visible_trace_is_not_a_candidate(tmp_path: Path) -> None:
    # A shell-only job leaves no trace of what it read or wrote.
    text = three_days(tmp_path, gp_chars=6000, gp_tool="Bash")
    assert not any(r.startswith("| - → - |") for r in candidates_table(text))


def test_main_session_work_an_existing_agent_can_do_points_to_it(
    tmp_path: Path,
) -> None:
    reader = agents.AgentDef(
        "tracker-reader", frozenset({"mcp__tracker__get_issue"}), "haiku"
    )
    text = three_days(
        tmp_path, main_chars=2000, per_turn=3, catalog={"tracker-reader": reader}
    )
    found = [r for r in finding(text, "A") if "`tracker`" in r]
    assert found and "`tracker-reader`" in found[0]


def test_writes_to_an_outside_system_are_not_reading_work(tmp_path: Path) -> None:
    text = three_days(tmp_path, main_chars=6000, main_tool="mcp__tracker__update_issue")
    assert not any("tracker" in r for r in candidates_table(text))


def test_a_small_weekly_saving_is_not_worth_an_agent(tmp_path: Path) -> None:
    high = Rules(min_weekly_tokens=10**9, big_result_chars=100, cold_write_tokens=10)
    rows = [
        r
        for r in candidates_table(three_days(tmp_path, main_chars=6000, rules=high))
        if "tracker" in r
    ]
    assert rows and "not worth it: small weekly saving" in rows[0]


def test_an_agent_granted_only_another_tool_of_the_system_is_not_named(
    tmp_path: Path,
) -> None:
    writer = agents.AgentDef(
        "tracker-writer", frozenset({"mcp__tracker__update_issue"}), "haiku"
    )
    text = three_days(
        tmp_path, main_chars=2000, per_turn=3, catalog={"tracker-writer": writer}
    )
    found = [r for r in finding(text, "A") if "`tracker`" in r]
    assert found and "tracker-writer" not in found[0]


def test_the_report_says_how_much_of_claude_codes_counter_it_holds(
    mini_corpus: Path, tmp_path: Path
) -> None:
    db = tmp_path / "m.sqlite"
    ingest(mini_corpus, db)
    text = why.render(sqlite3.connect(db), 30, LOW)
    line = next(r for r in text.splitlines() if "cost counter" in r)
    # The title request is in Claude Code's counter but in no transcript.
    assert "the transcripts hold" in line and "100%" not in line


def test_records_it_cannot_read_are_counted_and_warned_about(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    records = [
        at(assistant(0, f"req_{i}", SONNET, content=[], input_tokens=10), i)
        for i in range(3)
    ]
    records.insert(1, at({"type": "brand-new-kind"}, 1))
    # A cut-off line in the middle is broken; the same at the end is a live write.
    write_jsonl(root / PROJECT / f"{MAIN}.jsonl", records, tail='{"type": "ass')
    path = root / PROJECT / f"{MAIN}.jsonl"
    lines = path.read_text().splitlines(keepends=True)
    path.write_text("".join(lines[:2] + ['{"broken\n'] + lines[2:]))
    db = tmp_path / "h.sqlite"
    ingest(root, db)
    text = why.render(sqlite3.connect(db), 30, LOW)
    health = next(r for r in text.splitlines() if r.startswith("- Records read"))
    assert "1 cut-off line," in health
    assert "brand-new-kind" in health
    assert "**Warning:" in text.split("## Summary", 1)[0]


def test_until_builds_the_report_as_it_was_then(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{MAIN}.jsonl",
        [
            at(assistant(0, "req_0", SONNET, content=[], input_tokens=10), 1),
            at(assistant(0, "req_1", SONNET, content=[], input_tokens=10), 30),
        ],
    )
    db = tmp_path / "u.sqlite"
    ingest(root, db, until="2026-10-01T10:15:00.000Z")
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0] == 1


def test_a_streamed_record_without_usage_is_not_a_lost_answer(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    partial = at(assistant(0, "req_0", SONNET, content=[], input_tokens=10), 1)
    partial["message"] = {k: v for k, v in partial["message"].items() if k != "usage"}
    full = at(assistant(0, "req_0", SONNET, content=[], input_tokens=10), 1)
    write_jsonl(root / PROJECT / f"{MAIN}.jsonl", [partial, full])
    db = tmp_path / "s.sqlite"
    ingest(root, db)
    got = sqlite3.connect(db).execute("SELECT n FROM health WHERE what = 'no_usage'")
    assert got.fetchall() == []


def test_until_does_not_work_with_a_ready_ledger(tmp_path: Path) -> None:
    from tokenlab.cli import main

    db = tmp_path / "l.sqlite"
    create(db).close()
    assert main(["why", "--db", str(db), "--until", "2026-10-01"]) == 2


def test_earlier_runs_show_as_a_trend(conn: sqlite3.Connection) -> None:
    now: dict[str, Any] = {}
    why.render(conn, 30, LOW, out=now)
    assert set(now) == {"end", "days", "fixed_per_request", "cut_share", "angles"}
    old = {**now, "end": "2026-09-03T10:00:00.000Z", "fixed_per_request": 900}
    last = {**now, "end": "2026-09-24T10:00:00.000Z", "cut_share": 0.5}
    text = why.render(conn, 30, LOW, history=[old, last])
    trend = text.split("## Trend", 1)[1].split("## Summary", 1)[0]
    assert "| | 2026-09-03 (1 days) | 2026-09-24 (1 days) | now (1 days) |" in trend
    assert "| fixed part per request | 900 |" in trend
    assert "| cuttable waste (share of cost) | " in trend and "| 50% |" in trend


def test_no_history_no_trend(conn: sqlite3.Connection) -> None:
    assert "## Trend" not in why.render(conn, 30, LOW)


def test_each_run_adds_its_numbers_to_the_history(tmp_path: Path) -> None:
    from tokenlab.cli import main

    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{MAIN}.jsonl",
        [at(assistant(0, "req_0", SONNET, content=[], input_tokens=10), 1)],
    )
    hist = tmp_path / "h.jsonl"
    args = ["why", "--root", str(root), "--history", str(hist)]
    assert (
        main([*args, "--agents-dir", str(tmp_path), "--skills-dir", str(tmp_path)]) == 0
    )
    # Under two days of history there is no earlier point to measure.
    assert len(hist.read_text(encoding="utf-8").splitlines()) == 1
    assert (
        main([*args, "--agents-dir", str(tmp_path), "--skills-dir", str(tmp_path)]) == 0
    )
    assert len(hist.read_text(encoding="utf-8").splitlines()) == 2
    assert str(tmp_path) not in hist.read_text(encoding="utf-8")  # numbers only
    other = tmp_path / "none.jsonl"
    main(["why", "--root", str(root), "--history", str(other), "--no-history"])
    assert not other.exists()


def test_the_first_run_measures_an_earlier_point_once(tmp_path: Path) -> None:
    from tokenlab.cli import main

    root = tmp_path / "corpus"
    days = [
        {
            **assistant(0, f"req_{d}", SONNET, content=[], input_tokens=10),
            "timestamp": f"2026-10-{d:02d}T10:00:00.000Z",
        }
        for d in (1, 5, 9)
    ]
    write_jsonl(root / PROJECT / f"{MAIN}.jsonl", days)
    hist = tmp_path / "h.jsonl"
    common = ["--agents-dir", str(tmp_path), "--skills-dir", str(tmp_path)]
    assert main(["why", "--root", str(root), "--history", str(hist), *common]) == 0
    runs = [json.loads(x) for x in hist.read_text(encoding="utf-8").splitlines()]
    # Halfway through eight days: the baseline ends on the 5th.
    assert [r["end"][:10] for r in runs] == ["2026-10-05", "2026-10-09"]


def test_a_damaged_history_is_skipped_not_fatal(
    conn: sqlite3.Connection, tmp_path: Path
) -> None:
    from tokenlab.cli import MAX_RUNS, load_history, save_history

    hist = tmp_path / "h.jsonl"
    good = {
        "end": "2026-09-01T10:00:00",
        "fixed_per_request": 1,
        "cut_share": 0.1,
        "angles": {"R": 0.1},
        "days": 3,
    }
    lines = [good, {**good, "angles": None}, {**good, "cut_share": "x"}]
    hist.write_text(
        "".join(json.dumps(h) + "\n" for h in lines) + '{"end": "2026-09-02',
        encoding="utf-8",
    )
    save_history(hist, [{**good, "end": "2026-09-03T10:00:00.000Z"}])
    got = load_history(hist)
    # A naive and a Z time sort together; the half line did not eat the new one.
    assert [h["end"][:10] for h in got] == ["2026-09-01", "2026-09-03"]
    assert "## Trend" in why.render(conn, 30, LOW, history=got)
    save_history(hist, [good] * (MAX_RUNS + 5))
    assert len(hist.read_text(encoding="utf-8").splitlines()) == MAX_RUNS


def test_a_time_with_an_offset_is_read_as_utc() -> None:
    assert (
        why.when("2026-10-01T13:00:00+03:00").isoformat() == "2026-10-01T10:00:00+00:00"
    )
    assert why.when("2026-10-01T10:00:00").isoformat() == "2026-10-01T10:00:00+00:00"


def test_implausible_or_foreign_history_lines_are_skipped(tmp_path: Path) -> None:
    from tokenlab.cli import load_history

    good = {
        "end": "2026-09-01T10:00:00Z",
        "fixed_per_request": 1,
        "cut_share": 0.1,
        "angles": {"R": 0.1},
        "days": 3,
        "root": "abc",
    }
    bad = [
        {**good, "cut_share": True},
        {**good, "cut_share": 1.5},
        {**good, "fixed_per_request": -1},
        {**good, "angles": {"R": float("nan")}},
        {**good, "root": "other"},
    ]
    hist = tmp_path / "h.jsonl"
    lines = [json.dumps(h) for h in [good, *bad]]
    hist.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert len(load_history(hist, "abc")) == 1


def test_a_short_history_says_its_numbers_are_rough(conn: sqlite3.Connection) -> None:
    text = why.render(conn, 30, LOW)
    top = text.split("## Summary", 1)[0]
    assert "**Only 1 day of history" in top
    assert "token-lab " in text.splitlines()[0]
    # Findings name the days the data covers, not the 30 asked for.
    assert "in 30 days" not in text and "in 1 day" in text


def test_the_report_names_the_file_review_as_the_next_step(
    conn: sqlite3.Connection,
) -> None:
    first = why.render(conn, 30, LOW).split("## First step", 1)[1].split("## 1.", 1)[0]
    assert "**Next:** `/token-lab:optimize`" in first


def test_an_empty_history_prints_only_the_empty_message(tmp_path: Path) -> None:
    import contextlib
    import io

    from tokenlab.cli import main

    root = tmp_path / "projects"
    root.mkdir()
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert main(["why", "--root", str(root), "--no-history"]) == 0
    assert out.getvalue() == (
        "No Claude Code requests yet: try again after a few sessions.\n"
    )


def _corpus(tmp_path: Path, records: list[dict[str, Any]]) -> sqlite3.Connection:
    root = tmp_path / "corpus"
    write_jsonl(root / PROJECT / f"{MAIN}.jsonl", records)
    db = tmp_path / "x.sqlite"
    ingest(root, db)
    return sqlite3.connect(db)


def test_long_sessions_are_an_observation_not_cuttable(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{MAIN}.jsonl",
        [
            at(assistant(0, f"req_{i}", SONNET, content=[], read=300_000), i)
            for i in range(3)
        ],
    )
    # A subagent's request is not a long session, however large.
    sub = root / PROJECT / MAIN / "subagents"
    write_jsonl(
        sub / "agent-s1.jsonl",
        [at(assistant(0, "req_s", SONNET, content=[], read=900_000), 1)],
    )
    (sub / "agent-s1.meta.json").write_text(json.dumps({"agentType": "scout"}))
    db = tmp_path / "x.sqlite"
    ingest(root, db)
    text = why.render(sqlite3.connect(db), 30, LOW)
    row = next(r for r in text.splitlines() if r.startswith("| Long sessions |"))
    assert "1 of 1 session grew past 200 thousand" in row and "(observation" in row
    assert "the largest request carried 300 thousand" in row
    # A third of each main request (30,000 weighted) of 180,000 in all.
    assert "| 17% |" in row
    assert "the saving is not measured" in row
    assert "No cuttable waste above the threshold." in summary(text)
    # Not counted as cuttable waste and never the first step.
    first = text.split("## First step", 1)[1].split("**Next:**", 1)[0]
    assert "Long sessions" not in first


def test_a_skill_listed_twice_is_a_copy(tmp_path: Path) -> None:
    conn = _corpus(
        tmp_path,
        [
            att("skill_listing", 0, content="- tidy: a\n- plug:tidy: b"),
            at(
                assistant(
                    0, "req_0", SONNET, content=[use("t1", "Skill", skill="tidy")]
                ),
                1,
            ),
            at(assistant(0, "req_1", SONNET, content=[]), 2),
        ],
    )
    text = why.render(conn, 30, LOW)
    rows = finding(text, "S")
    assert any(
        "listed twice" in r and "`plug:tidy` (copy of `tidy`)" in r for r in rows
    )
    # The copy is not also reported as an unused skill.
    assert not any("plugin `plug`" in r for r in rows)


def test_two_mcp_servers_with_the_same_tools_overlap(tmp_path: Path) -> None:
    tools = [
        {"name": f"mcp__{s}__{t}", "description": "d" * 200}
        for s in ("grafana", "grafana-test")
        for t in ("query", "search", "list", "get")
    ]
    shape = {
        "type": "api-request-shape",
        "shapeHash": "h",
        "shape": {"system": [{"type": "text", "text": "s"}], "tools": tools},
    }
    conn = _corpus(
        tmp_path,
        [at(shape, 0), at(assistant(0, "req_0", SONNET, content=[]), 1)],
    )
    text = why.render(conn, 30, LOW)
    # Two environments of one service look the same: an observation, not cut.
    assert not any("same name" in r for r in finding(text, "T"))
    note = next(r for r in text.splitlines() if r.startswith("Observation (tools"))
    assert "MCP servers `grafana` and `grafana-test` offer 4 tools" in note
    assert "not counted as cuttable" in note
    # It shows in the Summary too, so it is not missed, with no cuttable share.
    row = next(
        r for r in summary(text).splitlines() if r.startswith("| Tools and MCP |")
    )
    assert "offer 4 tools of the same name (cost: the copies on" in row
    assert "(observation)" in row


def test_the_agents_line_says_how_much_subagents_carry(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{MAIN}.jsonl",
        [at(assistant(0, "req_0", SONNET, content=[], input_tokens=872), 0)],
    )
    sub = root / PROJECT / MAIN / "subagents"
    write_jsonl(
        sub / "agent-s1.jsonl",
        [
            # First request: 80 of 100 tokens from cache; paid 20 + 8.
            at(assistant(0, "req_1", SONNET, content=[], input_tokens=20, read=80), 1),
            at(assistant(0, "req_2", SONNET, content=[], read=1000), 2),
        ],
    )
    (sub / "agent-s1.meta.json").write_text(json.dumps({"agentType": "scout"}))
    db = tmp_path / "x.sqlite"
    ingest(root, db)
    row = next(
        r
        for r in summary(why.render(sqlite3.connect(db), 30, LOW)).splitlines()
        if r.startswith("| Agents |")
    )
    # 128 of 1,000 paid tokens.
    assert "subagents carry 13% of all cost, and only 80% of their first request" in row


def test_a_copy_below_the_cutoff_still_counts_as_unused(tmp_path: Path) -> None:
    records = [
        att(
            "skill_listing",
            0,
            content=f"- tidy: a\n- plug:tidy: {'b' * 400}\n- plug:other: {'c' * 400}",
        ),
        at(
            assistant(0, "req_0", SONNET, content=[use("t1", "Skill", skill="tidy")]), 1
        ),
        at(assistant(0, "req_1", SONNET, content=[]), 2),
    ]
    copy = next(
        r
        for r in finding(why.render(_corpus(tmp_path, records), 30, LOW), "S")
        if "listed twice" in r
    )
    weekly = int(copy.split("|")[3].replace(",", ""))
    # The copy alone is under the cutoff; with the other unused skill it is over.
    mid = Rules(min_weekly_tokens=weekly * 3 // 2, big_result_chars=100)
    (tmp_path / "again").mkdir()
    rows = finding(why.render(_corpus(tmp_path / "again", records), 30, mid), "S")
    assert any("plugin `plug`: 2 skills" in r for r in rows)
