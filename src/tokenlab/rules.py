"""Every report threshold and site-specific mapping, in one place.

The defaults are generic. A TOML file passed with `--rules` overrides them:

    [agents]             # numbers from Rules; [why] takes the why cutoffs
    min_recur = 5

    [[areas]]            # where a file lives; checked before the built-in areas
    name = "specs"
    path = "/specs/"

    [external]           # MCP server -> system: one job through two servers is one
    "mcp-atlassian" = "jira"
"""

import tomllib
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Rules:
    min_recur: int = 5  # calls before a job counts as recurring
    min_sessions: int = 3  # ... spread over at least this many sessions
    min_days: int = 3  # ... and this many days, so one fan-out is not a pattern
    retire_days: int = 60  # existing agents are judged on a longer window
    max_uses_to_retire: int = 1
    min_uses_to_narrow: int = 3  # enough calls to call a granted tool unused
    error_share: float = 0.5  # share of threads with a failed tool call → audit
    result_cap: int = 1500  # median final-answer tokens (thinking excluded) → tighten
    top_tools: int = 8  # tools listed per pilot proposal
    top_examples: int = 3  # descriptions listed per recurring job
    # An agent pays off when a call reads more than the summary it returns
    # (~1-2k tokens, Anthropic's multi-agent write-up) and its tools are fixed.
    agent_read_chars: int = 5_000  # median tool-result chars per call (main: message)
    scope_share: float = 0.8  # share of calls using only the job's core tools
    # `why` display cutoffs (chosen, not measured): below them a finding is not shown.
    min_weekly_tokens: int = 10_000  # estimated tokens per week
    big_result_chars: int = 20_000  # one tool result this large is a finding
    reread_min: int = 3  # the same file read this often in one thread
    cold_write_tokens: int = 20_000  # cache rewritten after an idle gap
    current_days: int = 2  # an item missing from sessions this recent is fixed
    max_candidates: int = 10  # rows in the agent candidates table
    areas: tuple[tuple[str, str], ...] = ()
    external: dict[str, str] = field(default_factory=dict)


DEFAULT = Rules()
_NUMBERS = {f.name for f in fields(Rules)} - {"areas", "external"}


def load(path: Path | None) -> Rules:
    if path is None:
        return DEFAULT
    data: dict[str, Any] = tomllib.loads(path.read_text(encoding="utf-8"))
    unknown = set(data) - {"agents", "why", "areas", "external"}
    numbers = {**data.get("agents", {}), **data.get("why", {})}
    unknown |= {f"agents.{k}" for k in set(numbers) - _NUMBERS}
    if unknown:
        raise ValueError(f"{path}: unknown keys {sorted(unknown)}")
    areas = tuple(
        (str(a["name"]), str(a["path"]).lower()) for a in data.get("areas", [])
    )
    external = {str(k): str(v) for k, v in data.get("external", {}).items()}
    return replace(DEFAULT, **numbers, areas=areas, external=external)
