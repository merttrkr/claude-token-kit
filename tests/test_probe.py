import json
from pathlib import Path

from tokenlab import probe

OUTPUT = json.dumps(
    {
        "session_id": "s-1",
        "total_cost_usd": 0.0072,
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 34_578,
            "cache_read_input_tokens": 25_696,
            "output_tokens": 4,
        },
    }
)

TRANSCRIPT = [
    {
        "type": "attachment",
        "attachment": {"type": "skill_listing"},
        "rendered": [{"content": "s" * 300}],
    },
    {
        "type": "attachment",
        "attachment": {"type": "skill_listing"},
        "rendered": [{"content": "s" * 20}],
    },
    {"type": "attachment", "attachment": {"type": "credential_org"}},
    {
        "type": "api-request-shape",
        "shapeHash": "h1",
        "shape": {
            "system": [
                {"type": "text", "text": "a" * 50},
                {"type": "text", "text": "b" * 7},
            ],
            "tools": [
                {"name": "Read", "description": "d" * 40},
                {"name": "Artifact", "description": "x" * 900},
            ],
        },
    },
    {"type": "api-request", "version": "2.1.319", "shapeHash": "h1"},
]


def write(tmp_path: Path) -> Path:
    f = tmp_path / "proj" / "s-1.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("".join(json.dumps(r) + "\n" for r in TRANSCRIPT))
    return tmp_path


def test_a_probe_row_carries_tokens_shape_and_parts(tmp_path: Path) -> None:
    projects = write(tmp_path)
    row = probe.parse("base", "haiku", ["--x"], OUTPUT, projects)
    assert row.prompt == 2 + 34_578 + 25_696
    assert (row.session_id, row.shape_hash, row.version) == ("s-1", "h1", "2.1.319")
    assert row.parts["attachment:skill_listing"] == 320
    assert row.parts["system"] == 57
    assert row.parts["tool:Read"] == len(
        json.dumps({"name": "Read", "description": "d" * 40})
    )
    assert "attachment:credential_org" not in row.parts


def test_rows_round_trip_through_jsonl(tmp_path: Path) -> None:
    row = probe.parse("base", "haiku", [], OUTPUT, write(tmp_path))
    log = tmp_path / "probes.jsonl"
    probe.append(log, row)
    probe.append(log, row)
    assert probe.load(log) == [row, row]


def test_trims_are_the_smaller_configs_on_the_baseline_model(tmp_path: Path) -> None:
    base = probe.parse("base", "haiku", [], OUTPUT, write(tmp_path))
    rows = [
        base,
        probe.Row(**{**base.__dict__, "label": "no-x", "prompt": base.prompt - 900}),
        probe.Row(**{**base.__dict__, "label": "more", "prompt": base.prompt + 50}),
        probe.Row(**{**base.__dict__, "label": "no-x", "model": "opus", "prompt": 1}),
    ]
    assert probe.trims(rows, "base") == [("no-x", 900)]


def test_a_row_without_a_transcript_shows_no_part_diff(tmp_path: Path) -> None:
    base = probe.parse("base", "haiku", [], OUTPUT, write(tmp_path))
    bare = probe.Row(**{**base.__dict__, "label": "root", "parts": {}})
    assert "chars:" not in probe.render([base, bare], baseline="base")


def test_the_table_flags_nondeterminism_and_shows_deltas(tmp_path: Path) -> None:
    projects = write(tmp_path)
    base = probe.parse("base", "haiku", [], OUTPUT, projects)
    smaller = probe.Row(
        **{
            **base.__dict__,
            "label": "no-artifact",
            "prompt": 60_000,
            "parts": {k: v for k, v in base.parts.items() if k != "tool:Artifact"},
        }
    )
    drifted = probe.Row(**{**base.__dict__, "prompt": base.prompt + 1})
    text = probe.render([base, drifted, smaller], baseline="base")
    assert "base" in text and "NOT deterministic" in text
    assert "no-artifact" in text and "-276" in text
    assert "tool:Artifact" in text
