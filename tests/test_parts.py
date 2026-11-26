"""Prompt parts: each attachment split into the files, skills, agents, servers and
tools it carries, so a report can say which one costs what."""

import sqlite3
from pathlib import Path
from typing import Any

from conftest import PROJECT, SONNET, assistant, ts, write_jsonl

from tokenlab.ingest import ingest

SESSION = "11110000-0000-0000-0000-000000000001"
# A slash command reaches the transcript as a user message, not a Skill tool call.
SLASH = (
    "<command-message>beta</command-message>\n<command-name>/plug:beta</command-name>"
)


def att(kind: str, **body: Any) -> dict[str, Any]:
    return {
        "type": "attachment",
        "timestamp": ts(1),
        "attachment": {"type": kind, **body},
        "rendered": [{"content": "r"}],
    }


def test_attachments_split_into_parts(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    write_jsonl(
        root / PROJECT / f"{SESSION}.jsonl",
        [
            att(
                "instructions",
                files=[
                    {
                        "path": "/h/.claude/CLAUDE.md",
                        "type": "User",
                        "content": "a" * 40,
                    },
                    {
                        "path": "/h/mem/MEMORY.md",
                        "type": "AutoMem",
                        "content": "b" * 90,
                    },
                ],
            ),
            att(
                "skill_listing",
                content="- alpha: does a\n- beta: does b, with: colon\n- plug:gamma: g",
                names=["alpha", "beta", "plug:gamma"],
            ),
            att("agent_listing_delta", addedTypes=["scout"], addedLines=["- scout: x"]),
            att(
                "mcp_instructions_delta", addedNames=["db"], addedBlocks=["## db\nuse"]
            ),
            att("deferred_tools_delta", addedNames=["Cron"], addedLines=["Cron"]),
            att("hook_additional_context", content=["h" * 60], hookName="SessionStart"),
            assistant(2, "req_1", SONNET, content=[], input_tokens=5),
            att(
                "nested_memory",
                path="/r/.claude/rules/x.md",
                content={"path": "/r/.claude/rules/x.md", "content": "c" * 70},
            ),
            att("invoked_skills", skills=[{"name": "alpha", "content": "d" * 300}]),
            {
                "type": "user",
                "timestamp": ts(2),
                "message": {"content": SLASH},
            },
            assistant(3, "req_2", SONNET, content=[], input_tokens=5),
        ],
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    rows = sqlite3.connect(db).execute(
        "SELECT request_seq, kind, name, chars FROM parts"
        " ORDER BY request_seq, kind, name"
    )
    assert rows.fetchall() == [
        (-1, "agent", "scout", len("- scout: x")),
        (-1, "deferred", "Cron", len("Cron")),
        (-1, "file", "/h/.claude/CLAUDE.md", 40),
        (-1, "file", "/h/mem/MEMORY.md", 90),
        (-1, "hook", "SessionStart", 60),
        (-1, "mcp", "db", len("## db\nuse")),
        (-1, "skill", "alpha", len("- alpha: does a")),
        (-1, "skill", "beta", len("- beta: does b, with: colon")),
        (-1, "skill", "plug:gamma", len("- plug:gamma: g")),
        (0, "command", "plug:beta", len(SLASH)),
        (0, "rule", "/r/.claude/rules/x.md", 70),
        (0, "skill_body", "alpha", 300),
    ]


def test_a_windows_path_is_one_part_whatever_its_slashes(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    records: list[dict[str, Any]] = []
    for n, sep in enumerate(("\\", "/"), 1):
        records += [
            att(
                "instructions",
                files=[{"path": f"C:{sep}r{sep}CLAUDE.md", "content": "a"}],
            ),
            att(
                "nested_memory",
                path=f"C:{sep}r{sep}x.md",
                content={"content": "c"},
            ),
            assistant(n + 1, f"req_{n}", SONNET, content=[], input_tokens=5),
        ]
    write_jsonl(root / PROJECT / f"{SESSION}.jsonl", records)
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    names = sqlite3.connect(db).execute(
        "SELECT DISTINCT name FROM parts WHERE kind IN ('file', 'rule') ORDER BY name"
    )
    assert names.fetchall() == [("C:/r/CLAUDE.md",), ("C:/r/x.md",)]
