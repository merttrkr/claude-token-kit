import json
import re
import subprocess
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
INSTALL_URL = re.compile(
    r"raw\.githubusercontent\.com/[^/\s]+/[^/\s]+/([^/\s]+)/install\.(?:ps1|sh)"
)


def version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def read_json(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / name).read_text(encoding="utf-8"))
    return data


def read_lines(name: str) -> list[str]:
    return (ROOT / name).read_text(encoding="utf-8").splitlines()


def block_between(name: str, start_marker: str, end_line: str) -> str:
    lines = read_lines(name)
    start = next(i for i, line in enumerate(lines) if start_marker in line)
    end = next(i for i in range(start + 1, len(lines)) if lines[i] == end_line)
    return "\n".join(lines[start + 1 : end]) + "\n"


def sh_block() -> str:
    return block_between("install.sh", "\"$KNOWN\" 2>/dev/null <<'EOF'", "EOF")


def ps1_block() -> str:
    return block_between("install.ps1", "$AutoUpdate = @'", "'@")


def run_block(path: Path) -> tuple[str, float]:
    started = time.monotonic()
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-", str(path)],
        input=sh_block(),
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return done.stdout.strip(), time.monotonic() - started


def test_version_is_the_same_everywhere() -> None:
    plugin = json.loads(
        (ROOT / ".claude-plugin/plugin.json").read_text(encoding="utf-8")
    )
    market = json.loads(
        (ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8")
    )
    lock = tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))
    locked = [p["version"] for p in lock["package"] if p["name"] == "tokenlab"]
    assert plugin["version"] == version()
    assert market["plugins"][0]["version"] == version()
    assert locked == [version()]


def test_readme_install_commands_point_to_the_release_tag() -> None:
    for name in ("README.md", "README.tr.md"):
        refs = INSTALL_URL.findall((ROOT / name).read_text(encoding="utf-8"))
        assert refs, name
        assert set(refs) == {f"v{version()}"}, name


def test_installers_embed_the_same_python() -> None:
    assert sh_block() == ps1_block()


def test_auto_update_turns_on_when_unset(tmp_path: Path) -> None:
    known = tmp_path / "known_marketplaces.json"
    other = {"autoUpdate": False, "source": {"repo": "a/b"}}
    known.write_text(
        json.dumps({"other": other, "token-kit": {"source": "x"}}), encoding="utf-8"
    )
    out, _ = run_block(known)
    assert out == "on"
    data = json.loads(known.read_text(encoding="utf-8"))
    assert data["token-kit"] == {"source": "x", "autoUpdate": True}
    assert data["other"] == other
    assert not Path(f"{known}.lock").exists()
    assert not Path(f"{known}.tmp").exists()


def test_auto_update_keeps_a_user_off(tmp_path: Path) -> None:
    known = tmp_path / "known_marketplaces.json"
    text = json.dumps({"token-kit": {"autoUpdate": False}})
    known.write_text(text, encoding="utf-8")
    out, _ = run_block(known)
    assert out == "user-off"
    assert known.read_text(encoding="utf-8") == text
    assert not Path(f"{known}.lock").exists()


def test_auto_update_missing_file_is_off(tmp_path: Path) -> None:
    out, _ = run_block(tmp_path / "known_marketplaces.json")
    assert out == "off"


def test_auto_update_gives_up_on_a_held_lock(tmp_path: Path) -> None:
    known = tmp_path / "known_marketplaces.json"
    text = json.dumps({"token-kit": {}})
    known.write_text(text, encoding="utf-8")
    Path(f"{known}.lock").mkdir()
    out, seconds = run_block(known)
    assert out == "off"
    assert seconds < 6 + 5  # the script waits about 5 s; the rest is interpreter start
    assert known.read_text(encoding="utf-8") == text


def test_every_check_named_in_principles_exists() -> None:
    """A principle is only applied if its check exists: a renamed or deleted test
    must update PRINCIPLES.md too."""
    text = (ROOT / "PRINCIPLES.md").read_text(encoding="utf-8")
    named = re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", text)
    named += [
        (f, n)
        for f, rest in re.findall(r"`(tests/[\w/]+\.py)::\w+`((?:, `\w+`)+)", text)
        for n in re.findall(r"`(\w+)`", rest)
    ]
    assert named
    for file, name in named:
        source = (ROOT / file).read_text(encoding="utf-8")
        assert re.search(rf"^def {name}\(", source, re.M), f"{file}::{name}"
