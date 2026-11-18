import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import OPUS, PROJECT, S1, S2, SONNET, assistant, ts, write_jsonl

from tokenlab import cli, episodes
from tokenlab.ingest import ingest


def user(second: int, content: Any, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": ts(second),
        "message": {"content": content},
        **extra,
    }


def human(second: int, text: str = "go") -> dict[str, Any]:
    return user(second, text, origin={"kind": "human"})


def call(tid: str, name: str, **inp: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid: str, text: str = "ok", *, error: bool = False) -> list[dict[str, Any]]:
    return [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}
    ]


def agent(
    sub: Path,
    name: str,
    second: int,
    meta: dict[str, Any],
    content: list[dict[str, Any]] | None = None,
    model: str = SONNET,
) -> None:
    write_jsonl(
        sub / f"{name}.jsonl",
        [
            assistant(
                second, f"r-{name}", model, content=content or [], input_tokens=10_000
            )
        ],
    )
    (sub / f"{name}.meta.json").write_text(json.dumps({"agentType": "x", **meta}))


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    root = tmp_path / "corpus"
    proj = root / PROJECT
    # S1: a request before any prompt, then a delegated episode with two parallel
    # roots (one nests), an interrupt, two bundled prompts (one a correction), and
    # an episode across a compaction.
    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            assistant(1, "r0", OPUS, content=[], input_tokens=50_000),
            human(2),
            assistant(
                3,
                "r1",
                OPUS,
                content=[
                    call("x1", "Read", file_path="/a"),
                    call("tu-a", "Agent", prompt="p"),
                    {
                        "type": "tool_use",
                        "id": "tu-c",
                        "name": "Agent",
                        "input": {"prompt": "p", "run_in_background": True},
                    },
                ],
                input_tokens=100_000,
            ),
            user(4, result("x1")),
            user(5, result("tu-a")),
            user(6, result("tu-c")),
            assistant(
                30,
                "r2",
                OPUS,
                content=[call("x2", "Read", file_path="/a")],
                input_tokens=1_000,
                read=109_000,
                output=500,
            ),
            user(31, result("x2", "PreToolUse:Read hook error: [G1:x]", error=True)),
            user(32, [{"type": "text", "text": "[Request interrupted by user]"}]),
            human(33, "first thought"),
            human(34, "hayır, öyle değil"),
            assistant(35, "r3", OPUS, content=[], input_tokens=1_000, read=110_000),
            {
                "type": "system",
                "subtype": "compact_boundary",
                "timestamp": ts(36),
                "compactMetadata": {"trigger": "auto"},
            },
            assistant(37, "r4", OPUS, content=[], input_tokens=30_000),
        ],
    )
    sub = proj / S1 / "subagents"
    # agent-b reuses the spawn id of its own call, which must not make it its own
    # parent; parentAgentId names the real one.
    agent(
        sub,
        "agent-a",
        10,
        {"toolUseId": "tu-a", "spawnDepth": 1},
        [call("tu-b", "Agent")],
    )
    agent(
        sub,
        "agent-b",
        20,
        {"toolUseId": "tu-b", "spawnDepth": 2, "parentAgentId": "a"},
        [call("tu-b", "Agent")],
    )
    agent(
        sub, "agent-c", 25, {"toolUseId": "tu-c", "spawnDepth": 1}, model="unpriced-1"
    )
    # S2: an SDK run.
    write_jsonl(
        proj / f"{S2}.jsonl",
        [
            user(40, "do it", turnOrigin="sdk", entrypoint="sdk-cli"),
            assistant(41, "r5", OPUS, content=[], input_tokens=20_000),
        ],
    )
    # S3: a prompt nothing answered, in a thread with no request at all.
    write_jsonl(proj / f"{S3}.jsonl", [human(42)])
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    return sqlite3.connect(db)


S3 = "33333333-0000-0000-0000-000000000003"


def cost(conn: sqlite3.Connection, request_id: str) -> float:
    return float(
        conn.execute(
            "SELECT cost_usd FROM requests WHERE request_id = ?", (request_id,)
        ).fetchone()[0]
    )


def test_lineage_uses_parent_agent_id_and_never_self(conn: sqlite3.Connection) -> None:
    tree = episodes.Tree(conn)
    assert tree.parent == {"agent-a": S1, "agent-b": "agent-a", "agent-c": S1}
    assert tree.unlinked == [] and tree.spawned_at["agent-a"] == 1


def test_episodes_bundle_prompts_and_own_their_requests(
    conn: sqlite3.Connection,
) -> None:
    audit = episodes.Audit()
    first, second, sdk = sorted(
        episodes.episodes(conn, 30, audit=audit), key=lambda e: e.start_ts
    )
    assert [r.seq for r in first.reqs] == [1, 2]
    assert [r.seq for r in second.reqs] == [3, 4] and second.prompts == 2
    assert sdk.source == "sdk" and first.source == second.source == "human"
    assert audit.bundled == 1 and audit.unowned_requests == 1
    assert first.roots == ["agent-a", "agent-c"] and first.max_parallel == 2
    assert first.nested == 1 and first.kind == "delegated"
    assert first.sub_inclusive == pytest.approx(
        cost(conn, "r-agent-a") + cost(conn, "r-agent-b")
    )
    assert (first.reads, first.rereads, first.hooks, first.errors) == (2, 1, 1, 1)
    assert first.interrupts == 1 and first.next_is_correction
    assert first.growth == 110_000 - 100_000 + 500
    # r1 dispatched; r2 ran after the last agent (agent-c, second 25) finished.
    assert first.main_before == pytest.approx(cost(conn, "r1"))
    assert first.main_during == 0
    assert first.main_after == pytest.approx(cost(conn, "r2"))
    assert first.wait_s == 22
    assert second.compacted and second.compaction_rewrites == 1
    assert second.misses == 0
    # A compaction inside the episode: growth and carry are null, not 0.
    assert (second.growth, second.carry_requests, second.carry_cost) == (
        None,
        None,
        None,
    )
    assert (first.carry_requests, sdk.growth) == (1, 0)  # r3; r4 is past the compaction


def test_audit_adds_up_and_names_exclusions(conn: sqlite3.Connection) -> None:
    text = episodes.render(conn)
    assert text == episodes.render(conn)
    rows = {
        line.split(" | ")[0][2:]: line.split(" | ")[1:]
        for line in text.splitlines()
        if line.startswith(("| every", "| episode cost", "| growth and carry"))
    }
    assert rows["every prompt turn is accounted for"] == [
        "excluded",
        "ledger: human 4, sdk 1; in episodes 4 (1 bundled with an earlier prompt)"
        " + no request followed 1 + in an episode begun before the window 0"
        " = 5 of 5; episodes: 3 |",
    ]
    assert rows["every main request is in an episode"][0] == "excluded"
    assert rows["every main request is in an episode"][1].startswith(
        "in episodes 5 + before any prompt 1 ("
    )
    assert rows["every main request is in an episode"][1].endswith("= 6 of 6 |")
    assert rows["every compaction event has a row"] == [
        "yes",
        "events in window: 1; rows: 1 (0 overlap another event, 1 with a partial"
        " window) |",
    ]
    assert rows["growth and carry are null exactly across a compaction"] == [
        "yes",
        "episodes with a compaction inside: 1; without growth: 1 |",
    ]
    assert rows["every agent is linked to its parent"][0] == "yes"
    assert rows["every interrupt is in an episode"] == [
        "yes",
        "1 of 1 seen; outside every episode: 0 (-) |",
    ]
    assert rows["every request has a price"][0] == "excluded"
    assert "models without a catalog price: unpriced-1" in text
    assert rows["episode cost adds up to the window"][0] == "yes"
    assert "unexplained $0.00" in text
    assert "human 2 (" in text and "sdk 1 (" in text
    # The compacted episode is out of the growth and carry medians, and the report
    # says how many: the lone inline 100-200k episode is that one.
    assert "no growth and no carry (null): 1 of 2 human episodes" in text
    assert "| inline | 100-200k | 1 | 2 | - |" in text
    assert "| delegated | 100-200k | 1 | 2 | 10,500 |" in text


def test_audit_says_no_when_the_walk_skips_a_thread(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The bug Codex found: a thread with no request in the window was skipped
    # before its prompts were counted. The ledger-side count must catch it.
    walk = episodes._thread_episodes

    def skipping(c: sqlite3.Connection, main: str, *rest: Any) -> Any:
        return [] if main == S3 else walk(c, main, *rest)

    monkeypatch.setattr(episodes, "_thread_episodes", skipping)
    assert "| every prompt turn is accounted for | NO |" in episodes.render(conn)


def test_audit_says_no_when_an_agent_is_lost(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Codex's probe: an agent missing from both the linked and the unlinked list
    # must turn the link check red, not green.
    class Losing(episodes.Tree):
        def __init__(self, c: sqlite3.Connection) -> None:
            super().__init__(c)
            del self.parent["agent-c"]

    monkeypatch.setattr(episodes, "Tree", Losing)
    text = episodes.render(conn)
    assert "| every agent is linked to its parent | NO |" in text
    assert "+ lost 1;" in text


def test_rows_and_edges_cannot_be_asked_together() -> None:
    with pytest.raises(SystemExit):
        cli.main(["episodes", "--rows", "--edges"])


def test_edges_list_every_agent_with_its_generation(conn: sqlite3.Connection) -> None:
    lines = [x.split("\t") for x in episodes.edges(conn).splitlines()]
    assert lines[0] == list(episodes.EDGE_FIELDS)
    bg = episodes.EDGE_FIELDS.index("background")
    assert [(x[2], x[3], x[4], x[bg]) for x in lines[1:]] == [
        (S1, "agent-a", "1", "0"),
        ("agent-a", "agent-b", "2", "0"),
        (S1, "agent-c", "1", "1"),
    ]


def test_main_cost_splits_around_agent_runs(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    proj = root / PROJECT
    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            human(1),
            assistant(2, "r1", OPUS, content=[call("tu-a", "Agent")], input_tokens=1),
            assistant(11, "gap", OPUS, content=[], input_tokens=1),
            assistant(12, "r2", OPUS, content=[call("tu-b", "Agent")], input_tokens=1),
            assistant(15, "during", OPUS, content=[], input_tokens=1),
            assistant(25, "after", OPUS, content=[], input_tokens=1),
        ],
    )
    sub = proj / S1 / "subagents"
    agent(sub, "agent-a", 10, {"toolUseId": "tu-a", "spawnDepth": 1})
    agent(sub, "agent-b", 20, {"toolUseId": "tu-b", "spawnDepth": 1})
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    conn = sqlite3.connect(db)
    (e,) = episodes.episodes(conn, 30)
    assert e.main_before == pytest.approx(cost(conn, "r1"))
    assert e.main_gap == pytest.approx(cost(conn, "gap"))
    assert e.main_during == pytest.approx(cost(conn, "r2") + cost(conn, "during"))
    assert e.main_after == pytest.approx(cost(conn, "after"))
    assert (e.wait_s, e.active_s) == (18, 16)  # runs 2-10 and 12-20


def test_rows_have_one_line_per_episode(conn: sqlite3.Connection) -> None:
    lines = episodes.rows(conn).splitlines()
    assert lines[0].split("\t") == list(episodes.ROW_FIELDS)
    assert len(lines) == 4  # three episodes; the unanswered prompt has none
    assert all(len(x.split("\t")) == len(episodes.ROW_FIELDS) for x in lines)


def test_rework_signals(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    proj = root / PROJECT
    edit = {"file_path": "/f.cs"}
    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            human(1),
            assistant(
                2,
                "r1",
                OPUS,
                content=[
                    {
                        "type": "tool_use",
                        "id": "e1",
                        "name": "Edit",
                        "input": {**edit, "old_string": "a", "new_string": "b"},
                    },
                    call("b1", "Bash", command="dotnet  test"),
                    call("tu-a", "Agent", prompt="p"),
                ],
                input_tokens=10_000,
            ),
            user(3, result("e1")),
            user(4, result("b1", "Exit code 1", error=True)),
            user(5, result("tu-a")),
            assistant(
                20,
                "r2",
                OPUS,
                content=[
                    {
                        "type": "tool_use",
                        "id": "e2",
                        "name": "Edit",
                        "input": {**edit, "old_string": "b", "new_string": "a"},
                    },
                    call("b2", "Bash", command="dotnet test"),
                    call("w1", "Write", file_path="/h.cs", content="z"),
                    {
                        "type": "tool_use",
                        "id": "m1",
                        "name": "MultiEdit",
                        "input": {
                            "file_path": "/i.cs",
                            "edits": [{"old_string": "p", "new_string": "q"}],
                        },
                    },
                    call("x1", "Read", file_path="/g.cs"),
                    call("x2", "Read", file_path="/j.cs"),
                ],
                input_tokens=10_000,
            ),
            user(21, result("e2")),
            user(22, result("b2", "Exit code 1", error=True)),
            user(23, result("x1")),
            user(24, result("x2")),
            human(40, "abcdef"),
            assistant(41, "r3", OPUS, content=[], input_tokens=10_000),
        ],
    )
    sub = proj / S1 / "subagents"
    agent(
        sub,
        "agent-a",
        10,
        {"toolUseId": "tu-a", "spawnDepth": 1},
        [
            call("y1", "Read", file_path="/g.cs"),
            call("y2", "Edit", file_path="/f.cs", old_string="x", new_string="y"),
        ],
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    first, _ = episodes.episodes(sqlite3.connect(db), 30)
    assert (first.edits, first.re_edits) == (4, 1)
    assert (first.shell_runs, first.retries, first.failed_again) == (2, 1, 1)
    # Edit reversal is counted over Edit calls only; Write and MultiEdit stay in
    # the mutation count but not in its denominator.
    assert (first.edits, first.edit_calls, first.reverts) == (4, 2, 1)
    # After agent-a finished (second 10): mutations e2 (/f.cs, which the agent
    # edited), w1 (/h.cs), m1 (/i.cs); reads x1 (/g.cs, which the agent read), x2.
    assert (first.after_mutations, first.mutation_rework) == (3, 1)
    assert (first.after_reads, first.read_overlap) == (2, 1)
    cells = episodes._signals([first]).split(" | ")
    assert cells[6:10] == ["50.0%", "50.0%", "33.3%", "50.0%"]  # reversal .. overlap
    head = episodes.SIGNAL_HEAD
    assert "exact Edit reversal" in head and "read overlap after agents" in head
    assert (first.next_chars, first.next_gap_s) == (6, 20)  # r2 ended at 20


def test_rows_leave_growth_and_carry_empty_across_a_compaction(
    conn: sqlite3.Connection,
) -> None:
    lines = [x.split("\t") for x in episodes.rows(conn).splitlines()]
    head = lines[0]
    by_start = {x[head.index("start_ts")]: x for x in lines[1:]}
    spanning = by_start[ts(33)]  # the bundle of two prompts, compaction inside
    plain = by_start[ts(2)]
    for name in ("growth", "carry_requests", "carry_usd"):
        assert spanning[head.index(name)] == ""
    assert [plain[head.index(n)] for n in ("growth", "carry_requests")] == [
        "10500",
        "1",
    ]


def test_rows_carry_the_prompt_source(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    proj = root / PROJECT

    def prompt(second: int, **origin: str) -> dict[str, Any]:
        return user(second, "go", **origin)

    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            prompt(1, turnOrigin="human", promptSource="typed"),
            assistant(2, "a", OPUS, content=[], input_tokens=1),
            prompt(3, turnOrigin="human", promptSource="queued"),
            assistant(4, "b", OPUS, content=[], input_tokens=1),
            prompt(5, turnOrigin="human"),
            assistant(6, "c", OPUS, content=[], input_tokens=1),
            prompt(7, turnOrigin="human", promptSource="suggestion_accepted"),
            assistant(8, "d", OPUS, content=[], input_tokens=1),
            prompt(9, turnOrigin="scheduled"),
            assistant(10, "e", OPUS, content=[], input_tokens=1),
            human(11),  # no turnOrigin or promptSource at all
            assistant(12, "f", OPUS, content=[], input_tokens=1),
        ],
    )
    write_jsonl(
        proj / f"{S2}.jsonl",
        [
            prompt(20, turnOrigin="sdk", promptSource="sdk", entrypoint="sdk-cli"),
            assistant(21, "g", OPUS, content=[], input_tokens=1),
        ],
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    lines = [x.split("\t") for x in episodes.rows(sqlite3.connect(db)).splitlines()]
    head = lines[0]
    got = [(x[head.index("source")], x[head.index("prompt_source")]) for x in lines[1:]]
    assert got == [
        ("human", "typed"),
        ("human", "queued"),
        ("human", "unknown"),
        ("human", "unknown"),  # an accepted suggestion is none of the four
        ("scheduled", "scheduled"),
        ("human", "unknown"),
        ("sdk", "sdk"),
    ]


def _compaction_ledger(tmp_path: Path) -> sqlite3.Connection:
    """S1: six requests, compaction A, four requests, compaction B, three requests.
    With K = 3 the windows of A (seqs 3-8) and B (seqs 7-12) share requests. S2:
    five requests, a compaction, one request: a partial window, nothing shared."""
    root = tmp_path / "corpus"
    proj = root / PROJECT

    def compact(second: int) -> dict[str, Any]:
        return {
            "type": "system",
            "subtype": "compact_boundary",
            "timestamp": ts(second),
            "compactMetadata": {"trigger": "auto"},
        }

    def edit(tid: str, old: str, new: str) -> dict[str, Any]:
        return call(tid, "Edit", file_path="/f.cs", old_string=old, new_string=new)

    def req(second: int, rid: str, tokens: int, *calls: dict[str, Any]) -> Any:
        return assistant(second, rid, OPUS, content=list(calls), input_tokens=tokens)

    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            human(1),
            req(2, "c1", 10_000),
            req(3, "c2", 10_000),
            req(4, "c3", 10_000),
            req(5, "c4", 40_000, call("x1", "Read", file_path="/a")),
            user(5, result("x1")),
            req(
                6,
                "c5",
                50_000,
                call("x2", "Read", file_path="/a"),
                edit("e1", "a", "b"),
                call("b1", "Bash", command="make"),
            ),
            user(6, result("x2")),
            user(6, result("e1")),
            user(6, result("b1", "Exit code 1", error=True)),
            req(
                7,
                "c6",
                60_000,
                edit("e2", "b", "a"),
                call("w1", "Write", file_path="/g.cs", content="z"),
                call("b2", "Bash", command="make"),
            ),
            user(7, result("e2")),
            user(7, result("w1")),
            user(7, result("b2", "Exit code 1", error=True)),
            user(7, [{"type": "text", "text": "[Request interrupted by user]"}]),
            compact(8),
            req(9, "c7", 20_000, call("h1", "Read", file_path="/a")),
            user(9, result("h1", "PreToolUse:Read hook error: [G1:x]", error=True)),
            human(10, "hayır, öyle değil"),
            req(
                10,
                "c8",
                25_000,
                call("x3", "Read", file_path="/b"),
                call("x4", "Read", file_path="/b"),
            ),
            user(10, result("x3")),
            user(10, result("x4")),
            req(11, "c9", 30_000),
            req(12, "c10", 35_000),
            compact(13),
            req(14, "c11", 40_000),
            req(15, "c12", 45_000),
            req(16, "c13", 50_000),
        ],
    )
    write_jsonl(
        proj / f"{S2}.jsonl",
        [req(20 + i, f"d{i}", 5_000) for i in range(5)]
        + [compact(25), req(26, "d5", 5_000)],
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    return sqlite3.connect(db)


def test_every_compaction_event_gets_a_row_with_fixed_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(episodes, "COMPACTION_K", 3)
    a, b, c = episodes.compactions(_compaction_ledger(tmp_path), 30)
    assert [(x.thread, x.ts) for x in (a, b, c)] == [
        (S1, ts(8)),
        (S1, ts(13)),
        (S2, ts(25)),
    ]
    assert [x.span for x in (a, b)] == [(3, 8), (7, 12)]
    assert [x.overlaps for x in (a, b, c)] == [True, True, False]
    assert [x.partial for x in (a, b, c)] == [False, False, True]
    assert [(len(x.before.prompts), len(x.after.prompts)) for x in (a, b, c)] == [
        (3, 3),
        (3, 3),
        (3, 1),
    ]
    # Median prompt, tool errors, hook blocks, rejected, rereads, re-edits, exact
    # Edit reversal, failed cmd failed again, interrupts per 100 requests, human
    # turns that correct.
    assert a.before.cells() == [
        "50,000",
        "28.6%",  # b1 and b2 of 7 results
        "0.0%",
        "0.0%",
        "50.0%",  # x2 re-reads /a: 1 of 2 Reads
        "33.3%",  # e2 re-edits /f.cs: 1 of 3 mutations (e1, e2, w1)
        "50.0%",  # e2 undoes e1: 1 of 2 Edit calls, the Write is not counted
        "50.0%",  # b2 runs the failed b1 again and fails: 1 of 2 shell runs
        "33.3",  # one interrupt over three requests
        "-",
    ]
    assert a.after.cells() == [
        "25,000",
        "33.3%",  # h1 of 3 results
        "33.3%",  # h1 is a hook block
        "0.0%",
        "33.3%",  # x4 re-reads /b: 1 of 3 Reads (h1 is new inside this window)
        "-",
        "-",
        "-",
        "0.0",
        "100.0%",  # the one human turn corrects
    ]
    # The before window of B shares c8 and c9 with the after window of A.
    assert b.before.cells()[0] == "30,000"
    assert b.after.cells() == ["45,000"] + ["-"] * 7 + ["0.0", "-"]


def test_compaction_table_states_k_and_marks_overlap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(episodes, "COMPACTION_K", 3)
    events = episodes.compactions(_compaction_ledger(tmp_path), 30)
    out = episodes._compaction_lines(events)
    text = "\n".join(out)
    assert "## Around compaction (3 compaction events, one row each)" in text
    assert "the 3 main requests of its thread before it" in text
    assert "Full windows, no overlap: 0; partial: 1; overlapping: 2." in text
    rows = [x.split(" | ") for x in out if x.startswith(("| 1 |", "| 2 |", "| 3 |"))]
    assert [(x[2], x[-2], x[-1]) for x in rows] == [
        ("3/3", "-", "yes |"),
        ("3/3", "-", "yes |"),
        ("3/1", "yes", "- |"),
    ]


def test_audit_says_no_when_an_event_has_no_row(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(episodes, "compactions", lambda *_: [])
    assert "| every compaction event has a row | NO |" in episodes.render(conn)


def test_report_header_says_shell_failure_is_is_error(conn: sqlite3.Connection) -> None:
    assert "no structured exit status" in episodes.render(conn)
