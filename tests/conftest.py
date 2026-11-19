import json
import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

PROJECT = "-Users-test-Development-proj"
S1 = "11111111-0000-0000-0000-000000000001"
S2 = "22222222-0000-0000-0000-000000000002"
START = int(datetime(2026, 10, 1, 10, 0, tzinfo=UTC).timestamp() * 1000)


# Tests run on fixtures and the frozen corpus only; a network call is a bug.
@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def guard(*args: object, **kwargs: object) -> None:
        raise RuntimeError("network access in tests")

    monkeypatch.setattr(socket, "socket", guard)
    yield


# CLI defaults point into the user's home (transcripts, agents, skills, history):
# tests see an empty home instead, so a missing argument never reads or writes it.
@pytest.fixture(autouse=True)
def _no_home_history(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from tokenlab import cli

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(cli, "PROJECTS", home / ".claude" / "projects")
    monkeypatch.setattr(
        cli, "HISTORY", home / ".claude" / "token-lab" / "history.jsonl"
    )


def ts(second: int) -> str:
    return f"2026-10-01T10:00:{second:02d}.000Z"


def assistant(
    second: int,
    request_id: str | None,
    model: str,
    *,
    content: list[dict[str, Any]],
    input_tokens: int = 0,
    output: int = 0,
    thinking: int = 0,
    read: int = 0,
    w5m: int = 0,
    w1h: int = 0,
    effort: str = "high",
) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": ts(second),
        "requestId": request_id,
        "effort": effort,
        "version": "2.1.314",
        "message": {
            "model": model,
            "content": content,
            "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": w5m + w1h,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": w5m,
                    "ephemeral_1h_input_tokens": w1h,
                },
                "output_tokens": output,
                "output_tokens_details": {"thinking_tokens": thinking},
                "server_tool_use": {"web_search_requests": 0},
                "speed": "standard",
            },
        },
    }


def usage(
    inp: int, out: int, read: int, w5m: int, w1h: int, cost: float
) -> dict[str, Any]:
    return {
        "inputTokens": inp,
        "outputTokens": out,
        "thinkingTokens": 0,
        "cacheReadInputTokens": read,
        "cacheCreationInputTokens": w5m + w1h,
        "cacheCreationEphemeral5mInputTokens": w5m,
        "cacheCreationEphemeral1hInputTokens": w1h,
        "webSearchRequests": 0,
        "costUSD": cost,
    }


def write_jsonl(path: Path, records: list[dict[str, Any]], tail: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records) + tail)


OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"
SHAPE = {
    "type": "api-request-shape",
    "timestamp": ts(1),
    "shapeHash": "h1",
    "shape": {
        "system": [
            {"type": "text", "text": "a" * 50},
            {"type": "text", "text": "b" * 30},
        ],
        "tools": [{"name": "Read", "description": "d" * 10}],
    },
}


@pytest.fixture
def mini_corpus(tmp_path: Path) -> Path:
    """Two sessions. S1 reconciles exactly and has a subagent and a background model;
    S2 compacts."""
    root = tmp_path / "corpus"
    proj = root / PROJECT
    write_jsonl(
        proj / f"{S1}.jsonl",
        [
            {
                "type": "attachment",
                "timestamp": ts(1),
                "attachment": {"type": "skill_listing", "content": "x" * 100},
                "rendered": [{"content": "y" * 120}],
            },
            {
                "type": "attachment",
                "timestamp": ts(1),
                "attachment": {"type": "credential_org"},
            },
            SHAPE,
            # One response streamed as two lines: the first carries partial usage.
            assistant(
                2,
                "req_1",
                OPUS,
                content=[{"type": "thinking", "thinking": "t"}],
                input_tokens=3,
                output=10,
                w1h=1000,
            ),
            assistant(
                3,
                "req_1",
                OPUS,
                content=[
                    {
                        "type": "tool_use",
                        "id": "tu1",
                        "name": "Read",
                        "input": {"file_path": "/x/a.py"},
                    }
                ],
                input_tokens=3,
                output=26,
                thinking=12,
                w1h=1000,
            ),
            {
                "type": "user",
                "timestamp": ts(4),
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu1",
                            "content": "z" * 600,
                        }
                    ]
                },
            },
            {**SHAPE, "timestamp": ts(4)},
            assistant(
                10,
                "req_2",
                OPUS,
                content=[{"type": "text", "text": "ok"}],
                input_tokens=2,
                output=40,
                read=1000,
                w1h=200,
            ),
            assistant(
                11,
                None,
                "<synthetic>",
                content=[{"type": "text", "text": "No response requested."}],
            ),
            {
                "type": "cost-state",
                "sessionId": S1,
                "startTime": START,
                "totalCostUSD": 0.0125,
                "modelUsage": {
                    OPUS: usage(5, 66, 1000, 0, 1200, 0.01114),
                    SONNET: usage(5, 50, 0, 300, 0, 0.00126),
                    HAIKU: usage(900, 20, 0, 0, 0, 0.0001),
                },
            },
        ],
        tail='{"type":"assistant","timest',
    )
    sub = proj / S1 / "subagents"
    write_jsonl(
        sub / "agent-a1.jsonl",
        [assistant(5, "req_3", SONNET, content=[], input_tokens=5, output=50, w5m=300)],
    )
    (sub / "agent-a1.meta.json").write_text(
        json.dumps(
            {
                "agentType": "test-writer",
                "description": "Write tests",
                "spawnDepth": 1,
                "toolUseId": "tu0",
            }
        )
    )
    write_jsonl(
        proj / f"{S2}.jsonl",
        [
            assistant(20, "req_4", OPUS, content=[], output=100),
            {
                "type": "system",
                "subtype": "compact_boundary",
                "timestamp": ts(30),
                "compactMetadata": {
                    "trigger": "auto",
                    "preTokens": 5000,
                    "postTokens": 25,
                },
            },
            {
                "type": "cost-state",
                "sessionId": S2,
                "startTime": START,
                "totalCostUSD": 0.0026,
                "modelUsage": {OPUS: usage(0, 130, 0, 0, 0, 0.0026)},
            },
        ],
    )
    return root
