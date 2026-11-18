"""Static probes: one trivial `claude -p` request per config, so a prompt-size change
is measured exactly instead of guessed.

Each probe runs in a dedicated folder (its own transcript slug), records the request's
prompt tokens from Claude Code's JSON output, and reads the session transcript for the
shapeHash and the size of every prefix part: system blocks, tool definitions and
attachments (skill and agent listings, CLAUDE.md, MCP instructions, ...).
"""

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

PROMPT = "Reply OK"
PART_TOP = 6


@dataclass(frozen=True)
class Row:
    label: str
    model: str
    args: list[str]
    session_id: str
    version: str
    shape_hash: str
    prompt: int  # input + cache read + cache write of the request
    output: int
    cost_usd: float
    parts: dict[str, int]  # chars per prefix part


def _parts(transcript: Path) -> tuple[dict[str, int], str, str]:
    parts: dict[str, int] = {}
    shape_hash = version = ""
    for line in transcript.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        kind = rec.get("type")
        if kind == "attachment":
            chars = sum(
                len(r.get("content", ""))
                for r in rec.get("rendered", [])
                if isinstance(r, dict)
            )
            if chars:
                key = f"attachment:{rec['attachment'].get('type', '?')}"
                parts[key] = parts.get(key, 0) + chars
        elif kind == "api-request-shape" and not shape_hash:
            shape = rec.get("shape", {})
            parts["system"] = sum(
                len(b.get("text", "")) for b in shape.get("system", [])
            )
            for tool in shape.get("tools", []):
                parts[f"tool:{tool.get('name', '?')}"] = len(json.dumps(tool))
        elif kind == "api-request" and not version:
            version = rec.get("version", "")
            shape_hash = rec.get("shapeHash", "")
    return parts, shape_hash, version


def parse(label: str, model: str, args: list[str], output: str, projects: Path) -> Row:
    out = json.loads(output)
    sid = out["session_id"]
    usage = out["usage"]
    files = sorted(projects.glob(f"*/{sid}.jsonl"))
    parts, shape_hash, version = _parts(files[0]) if files else ({}, "", "")
    return Row(
        label,
        model,
        args,
        sid,
        version,
        shape_hash,
        usage["input_tokens"]
        + usage["cache_read_input_tokens"]
        + usage["cache_creation_input_tokens"],
        usage["output_tokens"],
        out["total_cost_usd"],
        parts,
    )


def run(label: str, model: str, args: list[str], cwd: Path, projects: Path) -> Row:
    cmd = [
        "claude",
        "-p",
        PROMPT,
        "--output-format",
        "json",
        "--max-budget-usd",
        "0.5",
        "--model",
        model,
        *args,
    ]
    done = subprocess.run(  # noqa: S603
        cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return parse(label, model, args, done.stdout, projects)


def append(log: Path, row: Row) -> None:
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(row), sort_keys=True) + "\n")


def load(log: Path) -> list[Row]:
    if not log.exists():
        return []
    return [Row(**json.loads(line)) for line in log.read_text().splitlines() if line]


def trims(rows: list[Row], baseline: str) -> list[tuple[str, int]]:
    """Configs that shrink the prompt on the baseline's model: (label, tokens saved)."""
    base = next((r for r in rows if r.label == baseline), None)
    if base is None:
        return []
    first: dict[str, Row] = {}
    for r in rows:
        if r.model == base.model and r.label != baseline:
            first.setdefault(r.label, r)
    saved = [(label, base.prompt - r.prompt) for label, r in first.items()]
    return sorted(((lbl, n) for lbl, n in saved if n > 0), key=lambda x: (-x[1], x[0]))


def render(rows: list[Row], baseline: str) -> str:
    groups: dict[tuple[str, str], list[Row]] = {}
    for r in rows:
        groups.setdefault((r.label, r.model), []).append(r)
    base = {m: g[0] for (lbl, m), g in groups.items() if lbl == baseline}
    lines = [
        f"tokenlab probes (baseline: {baseline})",
        f"  {'label':28} {'model':8} {'runs':>4} {'prompt':>9} {'vs base':>9}"
        f" {'shapes':>6}  note",
    ]

    def order(key: tuple[str, str]) -> tuple[int, str, int, str]:
        b = base.get(key[1])
        delta = groups[key][0].prompt - b.prompt if b else 0
        return (key[0] != baseline, key[1], delta, key[0])

    for key in sorted(groups, key=order):
        g = groups[key]
        first = g[0]
        b = base.get(first.model)
        delta = f"{first.prompt - b.prompt:+,}" if b else "-"
        prompts = sorted({r.prompt for r in g})
        note = ""
        if len(prompts) > 1:
            note = f"NOT deterministic ({prompts[0]:,}..{prompts[-1]:,})"
        shapes = len({r.shape_hash for r in g})
        lines.append(
            f"  {first.label[:28]:28} {first.model[:8]:8} {len(g):>4}"
            f" {first.prompt:>9,} {delta:>9} {shapes:>6}  {note}".rstrip()
        )
        if b is not None and first.label != baseline and first.parts and b.parts:
            diffs = sorted(
                (
                    (first.parts.get(k, 0) - b.parts.get(k, 0), k)
                    for k in set(first.parts) | set(b.parts)
                    if first.parts.get(k, 0) != b.parts.get(k, 0)
                ),
                key=lambda x: (-abs(x[0]), x[1]),
            )[:PART_TOP]
            if diffs:
                lines.append(
                    "      chars: " + ", ".join(f"{k} {d:+,}" for d, k in diffs)
                )
    return "\n".join(lines) + "\n"
