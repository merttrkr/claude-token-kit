"""The optimize inventory: every loaded piece with its cost, the checks a script can
make for sure (missing references, repeated lines), and what could be added."""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import PROJECT, SONNET, assistant, write_jsonl

from tokenlab import cli, inventory
from tokenlab.ingest import ingest
from tokenlab.ledger import create
from tokenlab.rules import Rules

RULE = "Always run the full test suite before you call any task finished here."
SHARED = "Keep every answer short and name the file and line you changed in it."
SLASH = "<command-message>tidy</command-message>\n<command-name>/tidy</command-name>"
FIRST = {1: "devam", 2: "hayır, öyle değil", 3: SLASH}


def at(rec: dict[str, Any], day: int, minute: int) -> dict[str, Any]:
    return {**rec, "timestamp": f"2026-10-0{day}T10:{minute:02d}:00.000Z"}


def use(tid: str, name: str, **inp: str) -> dict[str, Any]:
    return {"type": "tool_use", "id": tid, "name": name, "input": inp}


def result(tid: str, chars: int, error: bool = False) -> dict[str, Any]:
    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": tid,
        "content": "x" * chars,
    }
    if error:
        block["is_error"] = True
    return {"type": "user", "message": {"content": [block]}}


@pytest.fixture
def setup(tmp_path: Path) -> tuple[sqlite3.Connection, Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    (home / "present.md").write_text("here", encoding="utf-8")
    claude_md = home / "CLAUDE.md"
    text = (
        f"# Rules\nSee `docs/missing.md` for details.\n{RULE}\n"
        f"Read `present.md` first.\n{SHARED}\n"
    )
    claude_md.write_text(text, encoding="utf-8")
    # A rule two folders below its project root names files from the root;
    # folder patterns and comment markers are no paths.
    (home / "proj" / "src" / "app").mkdir(parents=True)
    (home / "proj" / "src" / "app" / "main.py").write_text("x", encoding="utf-8")
    rules_dir = home / "proj" / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    rule_md = rules_dir / "r.md"
    rule_text = (
        "Edit `src/app/main.py` with care.\n"
        "Handlers live in `Events\\Subscribe\\`; comments start with `//`.\n"
        f"{SHARED}\n"
    )
    rule_md.write_text(rule_text, encoding="utf-8")
    root = tmp_path / "projects"
    for d in (1, 2, 3):
        sid = f"5555000{d}-0000-0000-0000-000000000000"
        records: list[dict[str, Any]] = [
            at(
                {
                    "type": "api-request-shape",
                    "shapeHash": f"h{d}",
                    "shape": {
                        "system": [{"type": "text", "text": f"You help. {RULE}"}],
                        "tools": [{"name": "Read", "description": "r"}],
                    },
                },
                d,
                0,
            ),
            at(
                {
                    "type": "attachment",
                    "attachment": {
                        "type": "instructions",
                        "files": [
                            {"path": str(claude_md), "type": "User", "content": text},
                            {
                                "path": str(rule_md),
                                "type": "Project",
                                "content": rule_text,
                            },
                        ],
                    },
                    "rendered": [{"content": text}],
                },
                d,
                0,
            ),
            at(
                {
                    "type": "attachment",
                    "attachment": {
                        "type": "skill_listing",
                        "content": "- tidy: cleans things",
                    },
                    "rendered": [{"content": "- tidy: cleans things"}],
                },
                d,
                0,
            ),
            at({"type": "user", "message": {"content": FIRST[d]}}, d, 1),
            at(
                assistant(
                    0,
                    f"req_{d}0",
                    SONNET,
                    content=[
                        use(f"r{d}", "Read", file_path="/w/notes.md"),
                        use(f"b{d}a", "Bash", command="make x"),
                        use(f"b{d}b", "Bash", command="make y"),
                    ],
                    input_tokens=100,
                ),
                d,
                2,
            ),
            at(result(f"r{d}", 300), d, 3),
            at(result(f"b{d}a", 50, error=True), d, 3),
            at(result(f"b{d}b", 50, error=True), d, 3),
            at(assistant(1, f"req_{d}1", SONNET, content=[], input_tokens=100), d, 4),
        ]
        write_jsonl(root / PROJECT / f"{sid}.jsonl", records)
    db = tmp_path / "ledger.sqlite"
    ingest(root, db)
    return sqlite3.connect(db), root, home


LOW = Rules(min_weekly_tokens=0, min_recur=3)


def section(text: str, title: str) -> str:
    return text.split(title, 1)[1].split("\n## ", 1)[0]


def report(setup: tuple[sqlite3.Connection, Path, Path]) -> str:
    conn, root, home = setup
    return inventory.render(
        conn, root, home / "agents", home / "skills", home / "plugins", 30, LOW
    )[0]


def test_the_system_prompt_comes_from_the_users_own_transcripts(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    assert RULE in inventory.system_prompt(setup[1])


def test_every_loaded_piece_is_listed_with_its_cost(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    pieces = section(report(setup), "## 1.")
    row = next(r for r in pieces.splitlines() if "CLAUDE.md" in r)
    assert "| CLAUDE.md |" in row or "CLAUDE.md" in row
    assert int(row.split(" | ")[4].replace(",", "")) > 0  # weekly cost


def test_a_missing_reference_is_found_and_a_present_one_is_not(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    stale = section(report(setup), "## 2.")
    assert "docs/missing.md" in stale and "CLAUDE.md:2" in stale
    assert "present.md" not in stale


def test_a_line_repeated_from_the_system_prompt_is_found(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    dup = section(report(setup), "## 3.")
    row = next(r for r in dup.splitlines() if "full test suite" in r.lower())
    assert "CLAUDE.md:3" in row and "system prompt" in row


def test_what_could_be_added(setup: tuple[sqlite3.Connection, Path, Path]) -> None:
    added = section(report(setup), "## 4.")
    errors = next(r for r in added.splitlines() if r.startswith("| Bash |"))
    assert "| 6 | 3 | 3 |" in errors
    reads = next(r for r in added.splitlines() if "/w/notes.md" in r)
    assert "| 3 | 3 |" in reads
    assert "1 correction (" in added
    assert json.dumps(PROJECT)[1:-1] in added  # where to read the correction


def test_the_command_writes_both_files(
    setup: tuple[sqlite3.Connection, Path, Path], tmp_path: Path
) -> None:
    _, root, home = setup
    out = tmp_path / "out"
    db = tmp_path / "ledger.sqlite"
    code = cli.main(
        [
            "inventory",
            "--root",
            str(root),
            "--db",
            str(db),
            "--out",
            str(out),
            "--agents-dir",
            str(home / "agents"),
            "--skills-dir",
            str(home / "skills"),
            "--plugins-dir",
            str(home / "plugins"),
        ]
    )
    assert code == 0
    assert "## 1." in (out / "inventory.md").read_text(encoding="utf-8")
    # Every piece, not only the most expensive ones, for a wider scope.
    assert "CLAUDE.md" in (out / "pieces.md").read_text(encoding="utf-8")
    assert RULE in (out / "system-prompt.md").read_text(encoding="utf-8")


def test_paths_resolve_from_the_project_root_and_patterns_are_skipped(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    stale = section(report(setup), "## 2.")
    assert "main.py" not in stale
    assert "Events" not in stale and "`//`" not in stale


def test_two_files_sharing_lines_show_as_one_pair(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    dup = section(report(setup), "## 3.")
    row = next(r for r in dup.splitlines() if "r.md" in r and "CLAUDE.md" in r)
    assert "| 1 |" in row and "keep every answer short" in row.lower()


def test_a_slash_command_counts_as_a_skill_use(
    setup: tuple[sqlite3.Connection, Path, Path],
) -> None:
    row = next(r for r in section(report(setup), "## 1.").splitlines() if "tidy" in r)
    assert "1 call " in row


def test_the_newest_system_prompt_wins_whatever_the_file_times(tmp_path: Path) -> None:
    root = tmp_path / "p"

    def shape(text: str, day: int) -> dict[str, Any]:
        return at(
            {
                "type": "api-request-shape",
                "shape": {"system": [{"type": "text", "text": text}], "tools": []},
            },
            day,
            0,
        )

    write_jsonl(root / "a" / "new.jsonl", [shape("newest prompt", 3)])
    write_jsonl(root / "b" / "old.jsonl", [shape("older prompt", 1)])
    # The older transcript was touched last.
    os.utime(root / "a" / "new.jsonl", (1_000_000, 1_000_000))
    assert inventory.system_prompt(root) == "newest prompt"


def test_plugin_versions_compare_as_numbers(tmp_path: Path) -> None:
    for version in ("9.0.0", "20.0.0"):
        skill = tmp_path / "cache" / "mk" / "plug" / version / "skills" / "s"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(version, encoding="utf-8")
    found = inventory._piece_file("skill", "plug:s", tmp_path, tmp_path, tmp_path)
    assert found is not None and "20.0.0" in str(found)


def test_paths_with_spaces_and_linux_roots_are_checked() -> None:
    assert inventory.BACKTICK.findall("see `docs/My File.md` here") == [
        "docs/My File.md"
    ]
    assert inventory._is_path("docs/My File.md")
    assert inventory._is_path("/root/notes.md")
    assert inventory._is_path("/workspace/app/main.py")
    assert not inventory._is_path("git commit -m done")


def test_an_empty_ledger_says_so(tmp_path: Path) -> None:
    text, pieces = inventory.render(
        create(tmp_path / "e.sqlite"), tmp_path, tmp_path, tmp_path, tmp_path
    )
    assert "No Claude Code requests yet" in text and pieces == ""


def test_old_inventory_folders_are_pruned_and_nothing_else(tmp_path: Path) -> None:
    from tokenlab.cli import STALE_INVENTORY_S, prune_inventories

    def folder(name: str, files: list[str], age: float) -> Path:
        d = tmp_path / name
        d.mkdir()
        for f in files:
            (d / f).write_text("x", encoding="utf-8")
        os.utime(d, (1e9 - age, 1e9 - age))
        return d

    old = folder(
        "token-lab-old", ["inventory.md", "system-prompt.md"], STALE_INVENTORY_S + 1
    )
    fresh = folder("token-lab-new", ["inventory.md"], 60)
    foreign = folder(
        "token-lab-mine", ["inventory.md", "notes.txt"], STALE_INVENTORY_S + 1
    )
    other = folder("other-old", ["inventory.md"], STALE_INVENTORY_S + 1)
    prune_inventories(tmp_path, 1e9)
    assert not old.exists()
    assert fresh.exists() and foreign.exists() and other.exists()


def test_a_folder_with_a_subfolder_is_not_pruned(tmp_path: Path) -> None:
    from tokenlab.cli import STALE_INVENTORY_S, prune_inventories

    d = tmp_path / "token-lab-x"
    (d / "inventory.md").mkdir(parents=True)  # our name, but a folder
    os.utime(d, (1e9 - STALE_INVENTORY_S - 1, 1e9 - STALE_INVENTORY_S - 1))
    prune_inventories(tmp_path, 1e9)
    assert (d / "inventory.md").is_dir()
