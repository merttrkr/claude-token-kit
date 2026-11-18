"""Token-budget gate: live probes against the ceilings in budgets.toml.

`main` runs in the probe folder, which has a transcript, so its parts can be
checked: the largest tool definition and named parts (skill listing, agent listing,
CLAUDE.md, system). `root` runs at the workspace root, where real sessions run and
the real MEMORY.md index loads; it leaves no transcript, so only the whole prompt
is checked. Both run with MCP off, the only deterministic setting.
"""

import tomllib
from dataclasses import dataclass
from pathlib import Path

from tokenlab.probe import Row


@dataclass(frozen=True)
class Place:
    cwd: Path  # relative to the lab
    persist: bool  # keep the transcript, so prefix parts can be read


SECTIONS = {
    "main": Place(Path("..") / "_tokenlab-probe", persist=True),
    "root": Place(Path(".."), persist=False),
}


def load(path: Path) -> dict[str, dict[str, int]]:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    return {
        str(section): {str(k): int(v) for k, v in ceilings.items()}
        for section, ceilings in data.items()
    }


def check(row: Row, ceilings: dict[str, int]) -> list[str]:
    """One line per ceiling the probe exceeds, in the ceilings' order."""
    over: list[str] = []
    for key, limit in ceilings.items():
        note = ""
        if key == "prompt_tokens":
            value = row.prompt
        elif key == "max_tool_chars":
            tools = {k: v for k, v in row.parts.items() if k.startswith("tool:")}
            name = max(tools, key=lambda k: (tools[k], k)) if tools else ""
            value, note = tools.get(name, 0), f" ({name})"
        else:
            value = row.parts.get(key, 0)
        if value > limit:
            over.append(f"{key} {value:,} > {limit:,}{note}")
    return over
