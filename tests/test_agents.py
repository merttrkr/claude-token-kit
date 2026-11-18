import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import PROJECT, SONNET, assistant, write_jsonl

from tokenlab import agents
from tokenlab.ingest import ingest
from tokenlab.rules import Rules

SESSIONS = [f"{n}{n}{n}{n}0000-0000-0000-0000-00000000000{n}" for n in (1, 2, 3)]


def tool(tid: str, name: str, **inp: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def on(day: int, rec: dict[str, Any]) -> dict[str, Any]:
    return {**rec, "timestamp": rec["timestamp"].replace("10-01", f"10-0{day}")}


def error_result(tid: str) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": "2026-10-01T10:00:59.000Z",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": "x",
                    "is_error": True,
                }
            ]
        },
    }


# (thread, session index = day, agentType, description, tools used, first tokens)
Thread = tuple[str, int, str, str, list[dict[str, Any]], int]
THREADS: list[Thread] = [
    (
        "g1",
        1,
        "general-purpose",
        "Find usages of Foo",
        [tool("x1", "Read", file_path="/a"), tool("x2", "Grep", pattern="Foo")],
        50000,
    ),
    *[
        (
            f"b{i}",
            1 + i % 3,
            "general-purpose",
            "Run dotnet tests for accounting",
            [
                tool(f"y{i}", "Bash", command="dotnet test"),
                tool(f"z{i}", "Edit", file_path="/src/Handler.cs"),
            ],
            56000,
        )
        for i in range(5)
    ],
    *[
        (
            f"j{i}",
            1 + i % 3,
            "general-purpose",
            "Jira snapshot PRJ-1",
            [
                tool(f"q{i}", "mcp__mcp-atlassian__jira_get_issue", issue_key="PRJ-1"),
                tool(f"p{i}", "Write", file_path="/r/specs/snap.md"),
            ],
            57000,
        )
        for i in range(4)
    ],
    # The same job through the other Jira server, written by a shell redirect.
    (
        "j4",
        3,
        "general-purpose",
        "Jira snapshot PRJ-2",
        [
            tool(
                "q4",
                "mcp__claude_ai_Atlassian_Rovo__getJiraIssue",
                issueIdOrKey="PRJ-2",
            ),
            tool("p4", "Bash", command="cat raw > /r/specs/snap.md"),
        ],
        57000,
    ),
    (
        "m1",
        1,
        "general-purpose",
        "Implement handler",
        [tool("w1", "Edit", file_path="/c/x.cs"), tool("w2", "mcp__srv__op")],
        58000,
    ),
    (
        "o1",
        2,
        "general-purpose",
        "hello there",
        [tool("h1", "Read", file_path="/e")],
        40000,
    ),
    *[
        (
            f"s{i}",
            1 + i % 3,
            "code-scout",
            "Locate Bar",
            [tool(f"r{i}", "Bash" if i == 0 else "Read", command="ls", file_path="/d")],
            19000,
        )
        for i in range(5)
    ],
    *[
        (
            f"f{i}",
            1 + i % 3,
            "code-finder",
            "Where is Baz",
            [tool(f"v{i}", "Read", file_path="/d")],
            21000,
        )
        for i in range(3)
    ],
]


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    root = tmp_path / "corpus"
    proj = root / PROJECT
    for day, session in enumerate(SESSIONS, start=1):
        mine = [t for t in THREADS if t[1] == day]
        spawn = [tool(f"tu-{t[0]}", "Agent", subagent_type=t[2]) for t in mine]
        write_jsonl(
            proj / f"{session}.jsonl",
            [
                on(
                    day,
                    assistant(
                        1,
                        f"req_main{day}",
                        SONNET,
                        content=spawn,
                        input_tokens=10,
                        output=5,
                    ),
                )
            ],
        )
        sub = proj / session / "subagents"
        for n, (t, _, a, desc, used, first) in enumerate(mine):
            scout = a == "code-scout"
            records = [
                assistant(
                    2 + n,
                    f"req_{t}_0",
                    SONNET,
                    content=used,
                    input_tokens=first,
                    output=10,
                ),
                *([error_result(used[0]["id"])] if scout or t.startswith("b") else []),
                assistant(
                    3 + n,
                    f"req_{t}_1",
                    SONNET,
                    content=[],
                    read=first,
                    output=2100 if scout else 40,
                    thinking=100 if scout else 0,
                ),
            ]
            write_jsonl(sub / f"agent-{t}.jsonl", [on(day, r) for r in records])
            (sub / f"agent-{t}.meta.json").write_text(
                json.dumps(
                    {"agentType": a, "description": desc, "toolUseId": f"tu-{t}"}
                )
            )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    return sqlite3.connect(db)


# Site areas and MCP systems come from a rules file; the tests use one like a real site.
RULES = Rules(
    areas=(("specs", "/specs/"),),
    external={"claude_ai_Atlassian_Rovo": "jira", "mcp-atlassian": "jira", "pg": "db"},
)

CATALOG = {
    "code-scout": agents.AgentDef(
        "code-scout", frozenset({"Read", "Grep", "Glob"}), "haiku"
    ),
    "build-test-runner": agents.AgentDef(
        "build-test-runner", frozenset({"Read", "Bash"}), "sonnet"
    ),
    "quick-build": agents.AgentDef("quick-build", frozenset({"Read", "Bash"}), "haiku"),
    "jira-reader": agents.AgentDef("jira-reader", frozenset({"Read"}), "haiku"),
    "code-finder": agents.AgentDef("code-finder", frozenset({"Read"}), "haiku"),
    "lonely": agents.AgentDef("lonely", frozenset({"Read"}), None),
}


@pytest.mark.parametrize(
    ("path", "area"),
    [
        (r"C:\Repos\specs\x\spec.md", "specs"),
        ("/c/Users/me/.claude/skills/x/SKILL.md", "claude-config"),
        (r"C:\Users\me\AppData\Local\Temp\claude\s\scratchpad\a.py", "scratch"),
        ("/src/A.cs", "code:cs"),
        (r"C:\Repos\svc\temp\Handler.cs", "code:cs"),
        ("/notes/snap.md", "file"),
        (None, "file"),
    ],
)
def test_area_of_a_path(path: str | None, area: str) -> None:
    assert agents.area(path, RULES) == area


@pytest.mark.parametrize(
    ("used", "output"),
    [
        # Scratch is a means: the output is the area written most outside it.
        (
            [
                ("Write", "/t/scratchpad/a.py"),
                ("Write", "/t/scratchpad/b.py"),
                ("Write", "/r/specs/x.md"),
            ],
            "specs",
        ),
        ([("Write", "/t/scratchpad/a.py")], "scratch"),
        # A shell redirect counts only when nothing was written natively.
        ([("Bash", "dotnet test > out.txt"), ("Edit", "/src/A.cs")], "code:cs"),
        ([("Bash", "cat raw > /r/specs/s.md")], "specs"),
        # A tie goes to the area that names the job best.
        ([("Write", "/n.md"), ("Edit", "/b.ts")], "code:ts"),
        ([("Edit", "/b.ts"), ("Edit", "/a.cs")], "code:cs"),
        ([("Edit", "/a.cs"), ("Edit", "/b.ts")], "code:cs"),
        ([("Read", "/a.cs")], None),
        ([("Bash", "dotnet test > /dev/null 2>&1")], None),
        # A log or a relative path is no output: `dotnet test > log.txt`.
        ([("Bash", "jq . raw > out.json")], None),
        ([("Bash", "cd /c/Repos/svc.x && dotnet test > /c/t/log.txt")], None),
        # A script written to scratch that writes elsewhere: the command names where.
        (
            [
                ("Bash", "cd /c/Repos/specs/x; cat > $TEMP/s3.txt <<'EOF'"),
                ("Write", r"C:\Users\me\AppData\Local\Temp\c\scratchpad\ins.py"),
            ],
            "specs",
        ),
        # The same on macOS and Linux paths; a URL is no path.
        (
            [
                ("Bash", "cd /Users/me/repo/specs/x; cat > $TMPDIR/s3.txt <<'EOF'"),
                ("Write", "/private/var/folders/x/T/claude/scratchpad/ins.py"),
            ],
            "specs",
        ),
        ([("Bash", "cd /home/me/specs/x && echo hi > note.txt")], "specs"),
        ([("Bash", "curl https://x.io/specs/a > out.json")], None),
        ([("Bash", "grep -l x src/**/*.cs > /c/t/log.txt")], None),
        # A pattern is no path: `"a|/\.claude/"` names no area.
        ([("Bash", r'grep -v -E "wt|/\.claude/" /tmp/a.txt > /tmp/b.txt')], "scratch"),
        ([("Bash", "sed -i 's#/tmp/#/specs/#' local.txt")], None),
    ],
)
def test_output_area(used: list[tuple[str, str | None]], output: str | None) -> None:
    assert agents.output_area(used, RULES) == output


def test_calls_carry_signature_session_and_answer(conn: sqlite3.Connection) -> None:
    by_id = {c.thread_id: c for c in agents.calls(conn, 30, RULES)}
    # A job is what it writes; a job that writes nothing is what it reads most.
    assert by_id["agent-g1"].signature == "file → -"
    assert by_id["agent-o1"].signature == "file → -"
    assert by_id["agent-b0"].signature == "- → code:cs"
    assert by_id["agent-j0"].signature == "jira → specs"
    assert by_id["agent-j4"].signature == "jira → specs"
    assert by_id["agent-m1"].signature == "srv → code:cs"
    assert by_id["agent-s1"].final_text == 2000  # output minus thinking
    assert by_id["agent-s1"].errored and not by_id["agent-g1"].errored
    assert {by_id[f"agent-b{i}"].session for i in range(3)} == set(SESSIONS)


def test_cache_and_cost_come_from_the_ledger(conn: sqlite3.Connection) -> None:
    s1 = {c.thread_id: c for c in agents.calls(conn, 30, RULES)}["agent-s1"]
    # First request reads nothing from cache; the second reads the first's prompt.
    assert (s1.first_read, s1.total_read, s1.total_prompt) == (0, 19000, 38000)
    priced = conn.execute(
        "SELECT SUM(cost_usd) FROM requests WHERE thread_id = 'agent-s1'"
    ).fetchone()[0]
    assert s1.total_cost == pytest.approx(priced) and 0 < s1.first_cost < priced
    text = agents.render(conn, CATALOG, frozenset(), rules=RULES)
    row = next(r for r in text.splitlines() if r.startswith("| code-scout | 5 | 3 |"))
    assert row.split(" | ")[4:6] == ["0%", f"{s1.first_cost:.3f}"]
    assert row.split(" | ")[7] == "50%"


def test_routing_needs_the_same_job_and_the_tools(
    conn: sqlite3.Connection,
) -> None:
    text = agents.render(conn, CATALOG, frozenset({"jira-reader"}), rules=RULES)
    # Matched on what the work did, so "hello there" counts too.
    assert "| code-scout | 2 | 2 | 45,000 | 19,000 | 52,000 |" in text
    # Same job, but g1 also used Grep, which no "file → -" agent is granted here.
    narrow = {
        **CATALOG,
        "code-scout": agents.AgentDef("code-scout", frozenset({"Read"}), "haiku"),
    }
    text = agents.render(conn, narrow, frozenset(), rules=RULES)
    assert "Not routed, same job, but used tools the agent lacks: 1" in text


def test_recurring_jobs_need_sessions_and_days(conn: sqlite3.Connection) -> None:
    text = agents.render(conn, CATALOG, frozenset(), rules=RULES)
    assert "| - → code:cs | 5 | 3 | 3 | 56,000 |" in text
    assert "| jira → specs | 5 | 3 | 3 | 57,000 |" in text
    assert "Jira snapshot PRJ-1 (4), Jira snapshot PRJ-2 (1) |" in text
    assert "Stays generic, not recurring: 1 calls" in text
    assert "intent" not in text
    assert "saving" not in text.split("## Recurring")[1].split("## Existing")[0]


def test_recurring_jobs_show_tool_errors(conn: sqlite3.Connection) -> None:
    # A new agent's definition must not inherit the generic runs' failures unseen.
    rows = agents.render(conn, CATALOG, frozenset(), rules=RULES).splitlines()
    head = next(r for r in rows if r.startswith("| job |")).split(" | ")
    col = head.index("tool errors (calls)")
    code = next(r for r in rows if r.startswith("| - → code:cs |")).split(" | ")
    jira = next(r for r in rows if r.startswith("| jira → specs |")).split(" | ")
    assert (code[col], jira[col]) == ("Bash 5", "none")


def test_existing_agent_verdicts(conn: sqlite3.Connection) -> None:
    text = agents.render(conn, CATALOG, frozenset({"jira-reader"}), rules=RULES)
    assert "| lonely | 0 | 0 | 0 | retire | calls: 0 |" in text
    assert "| jira-reader | 0 | 0 | 0 | retire | calls: 0; a skill names it" in text
    assert "| code-finder | 3 | 3 | 3 | merge | same job as code-scout |" in text
    assert "| quick-build | 0 | 0 | 0 | retire | calls: 0 |" in text
    assert (
        "| code-scout | 5 | 3 | 3 | definition drift | used, not granted now: Bash |"
        in text
    )
    assert (
        "| code-scout | 5 | 3 | 3 | audit errors | 5/5 threads had a tool error |"
        in text
    )
    assert (
        "| code-scout | 5 | 3 | 3 | tighten contract | median answer 2,000 tokens |"
        in text
    )


def test_meta_tools_do_not_count_and_writing_work_never_gets_haiku() -> None:
    assert "SubagentHandback" in agents.IGNORED_TOOLS
    assert agents._model("file") == "sonnet"
    assert agents._model(None) == "haiku"


def test_render_is_byte_identical(conn: sqlite3.Connection) -> None:
    assert agents.render(conn, CATALOG, frozenset(), rules=RULES) == agents.render(
        conn, CATALOG, frozenset(), rules=RULES
    )


def test_parse_agent_reads_tools_and_model(tmp_path: Path) -> None:
    f = tmp_path / "x.md"
    f.write_text(
        "---\nname: x\ndescription: d\nmodel: haiku\n"
        "tools: Read, Grep, Agent(code-scout)\n---\nbody\n",
        encoding="utf-8",
    )
    assert agents.parse_agent(f) == agents.AgentDef(
        "x", frozenset({"Read", "Grep", "Agent"}), "haiku"
    )
    g = tmp_path / "y.md"
    g.write_text("---\nname: y\ndescription: d\n---\n", encoding="utf-8")
    assert agents.parse_agent(g).tools is None


def test_merge_needs_the_same_model(conn: sqlite3.Connection) -> None:
    deep = {
        **CATALOG,
        "code-finder": agents.AgentDef("code-finder", frozenset({"Read"}), "opus"),
    }
    text = agents.render(conn, deep, frozenset(), rules=RULES)
    assert "same job as" not in text


def test_a_job_with_no_visible_trace_is_no_job(conn: sqlite3.Connection) -> None:
    by_id = {c.thread_id: c for c in agents.calls(conn, 30, RULES)}
    assert by_id["agent-s0"].signature == "- → -"  # shell only
    assert agents._job([by_id["agent-s0"]]) is None
    assert agents._job([by_id["agent-s1"]]) == "file → -"


@pytest.mark.parametrize(
    ("used", "found"),
    [
        ([("mcp__pg__query", None), ("WebFetch", "https://x")], {"db", "web"}),
        # An API read through a shell is a read too: it splits E2E runs from docs.
        ([("Bash", "curl -s http://localhost:5000/api/x")], {"http"}),
        ([("PowerShell", "Invoke-RestMethod $u -Headers $h")], {"http"}),
        ([("Bash", "git log -1")], set()),
    ],
)
def test_externals_include_shell_http(
    used: list[tuple[str, str | None]], found: set[str]
) -> None:
    assert agents.externals(used, RULES) == found
