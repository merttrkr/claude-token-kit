import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import OPUS, PROJECT, S1, assistant, ts, write_jsonl

from tokenlab.ingest import ingest, turn_kind, turn_source


def user(second: int, content: Any, **extra: Any) -> dict[str, Any]:
    return {
        "type": "user",
        "timestamp": ts(second),
        "message": {"content": content},
        **extra,
    }


def result(tid: str, text: str, *, error: bool = False) -> list[dict[str, Any]]:
    return [
        {"type": "tool_result", "tool_use_id": tid, "content": text, "is_error": error}
    ]


def call(tid: str, name: str, **inp: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def test_turns_results_and_spawns(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    proj = root / PROJECT
    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            user(1, "fix the handler", origin={"kind": "human"}),
            assistant(
                2,
                "r1",
                OPUS,
                content=[
                    call("t1", "Edit", file_path="/a.cs"),
                    call("t2", "Bash", command="git commit"),
                    call("t3", "Agent", subagent_type="x", prompt="p" * 42),
                    call("t4", "Write", file_path="/b.md"),
                ],
            ),
            user(
                3,
                result("t1", "PreToolUse:Edit hook error: [G1:no-throw] x", error=True),
            ),
            user(
                4,
                result("t2", "Permission for this action denied", error=True),
                permissionDecision={"decision": "reject", "source": "config"},
            ),
            user(
                5,
                result("t4", "ok"),
                permissionDecision={"decision": "accept", "source": "user_temporary"},
            ),
            user(6, [{"type": "text", "text": "[Request interrupted by user]"}]),
            user(7, "Hayır, öyle değil"),
            user(8, "<task-notification> done", origin={"kind": "task-notification"}),
            user(9, "Caveat: local commands", isMeta=True),
            user(10, "This session is being continued", isCompactSummary=True),
            user(11, "Another Claude session sent a message"),
        ],
    )
    sub = proj / S1 / "subagents"
    write_jsonl(sub / "agent-a1.jsonl", [assistant(12, "r2", OPUS, content=[])])
    (sub / "agent-a1.meta.json").write_text(
        json.dumps(
            {
                "agentType": "x",
                "toolUseId": "t3",
                "spawnDepth": 2,
                "parentAgentId": "a0",
                "requestShape": "shape-1",
            }
        )
    )
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    conn = sqlite3.connect(db)

    assert conn.execute(
        "SELECT request_seq, kind, chars, correction FROM turns ORDER BY ts"
    ).fetchall() == [
        (-1, "human", 15, 0),
        (0, "interrupt", 29, 0),
        (0, "human", 17, 1),
        (0, "task_notification", 24, 0),
        (0, "meta", 22, 0),
        (0, "compact_summary", 31, 0),
        (0, "peer", 37, 0),
    ]
    assert conn.execute(
        "SELECT tool_use_id, decision, ts FROM content WHERE kind = 'tool_result'"
        " ORDER BY tool_use_id"
    ).fetchall() == [
        ("t1", "hook:G1", ts(3)),
        ("t2", "reject:config", ts(4)),
        ("t4", "approved:user", ts(5)),
    ]
    assert conn.execute(
        "SELECT tool, brief FROM content WHERE kind = 'tool_use' ORDER BY tool_use_id"
    ).fetchall() == [("Edit", None), ("Bash", None), ("Agent", 42), ("Write", None)]
    assert conn.execute(
        "SELECT parent_agent_id, request_shape FROM threads WHERE kind = 'subagent'"
    ).fetchone() == ("a0", "shape-1")


@pytest.mark.parametrize(
    ("rec", "kind"),
    [
        (
            {
                "turnOrigin": "human",
                "promptSource": "typed",
                "origin": {"kind": "human"},
            },
            "human",
        ),
        (
            {
                "turnOrigin": "human",
                "promptSource": "queued",
                "origin": {"kind": "human"},
            },
            "human",
        ),
        ({"turnOrigin": "sdk", "promptSource": "sdk", "entrypoint": "sdk-cli"}, "sdk"),
        ({"entrypoint": "sdk-cli"}, "sdk"),
        ({"turnOrigin": "scheduled"}, "scheduled"),
        (
            {"turnOrigin": "task_notification", "promptSource": "system"},
            "task_notification",
        ),
        ({"origin": {"kind": "task-notification"}}, "task_notification"),
        ({}, "human"),
    ],
)
def test_turn_kind_reads_the_source_fields(rec: dict[str, Any], kind: str) -> None:
    assert turn_kind(rec, "some text") == kind
    assert turn_source({"turnOrigin": "sdk"}) == "sdk/-/-"
