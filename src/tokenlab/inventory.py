"""Inventory for `/token-lab:optimize`: what a model reads against the placement
standard, and the checks a script can make for sure before it does.

1. Pieces: every CLAUDE.md, rule, memory index, skill and agent the transcripts show
   loaded, with its file, size, weekly cost and use, most expensive first.
2. Missing references: paths a piece names that are not on disk.
3. Repeated lines: a sentence found in two pieces, or in a piece and the system prompt.
4. What could be added: tool errors that keep coming back (hook candidates), files
   read again in many sessions (memory or CLAUDE.md candidates), and where the
   user's corrections are, for the model to read.

Deterministic for the same ledger, rules and files on disk. Prompts are never printed;
the system prompt is the one Claude Code itself sent, taken from the user's own
transcripts.
"""

import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from tokenlab.rules import DEFAULT, Rules
from tokenlab.why import Carry, _n, _window, tokens_per_char, weighted

KIND = {"file": "file", "rule": "rule", "skill": "skill", "agent": "agent"}
BACKTICK = re.compile(r"`([^`\n]+)`")
MD_TARGET = re.compile(r"\]\(([^)\s#]+)\)")
FILE_EXT = re.compile(r"\.(?:md|py|ps1|sh|json|toml|ya?ml|mjs|js|ts|cs|txt)$")
GIT_BASH_DRIVE = re.compile(r"^/([a-zA-Z])/")
UNIX_ROOT = re.compile(
    r"^/(?:Users|home|root|tmp|var|opt|etc|usr|private|mnt|Volumes|srv|data"
    r"|workspace|workspaces|Applications|Library|nix|snap)/"
)
NEWEST_FILES = 20  # transcripts searched for the newest system prompt
SENTENCE = re.compile(r"(?<=[.!?])\s+")
LEAD = re.compile(r"^[\s>*#\-\d.)]+")
MIN_SENTENCE = 40  # shorter lines repeat by chance ("Read the file first.")
MAX_ROWS = 15  # rows per list in section 4
MAX_PIECES = 30  # pieces listed in section 1; the rest are summed
MAX_STALE = 40  # missing references listed
MAX_PAIRS = 20  # file pairs listed in section 3
# A full MCP tool name; a pattern such as `mcp__srv__browser_*` is not one.
TOOL_REF = re.compile(
    r"\bmcp__[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*__[A-Za-z0-9_-]*[A-Za-z0-9](?![\w*-])"
)
# A MEMORY.md list line longer than this is a record, not a pointer: the index
# lines the maintainer wrote as pointers are under 200 characters.
LONG_INDEX_LINE = 200


def system_prompt(root: Path) -> str:
    """The system blocks of the newest request shape, by its own time, among
    the most recently written main transcripts."""
    files = sorted(
        (p for p in root.rglob("*.jsonl") if "subagents" not in p.parts),
        key=lambda p: (p.stat().st_mtime, str(p)),
        reverse=True,
    )[:NEWEST_FILES]
    best: tuple[str, str] | None = None
    for path in files:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"api-request-shape"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                blocks = (rec.get("shape") or {}).get("system") or []
                text = "\n\n".join(
                    str(b.get("text") or "") for b in blocks if isinstance(b, dict)
                )
                stamp = str(rec.get("timestamp") or "")
                if text and (best is None or stamp > best[0]):
                    best = (stamp, text)
    return best[1] if best else ""


def _norm(sentence: str) -> str:
    s = LEAD.sub("", sentence).replace("`", "").replace("**", "").lower()
    return " ".join(s.split()).rstrip(".:;")


def _exists(ref: str, base: Path) -> bool:
    """A relative path is looked up in the piece's folder and three folders up: a
    rule under `.claude/rules/` names files from its project root."""
    ref = ref.split("#", 1)[0]  # a heading anchor
    m = GIT_BASH_DRIVE.match(ref)
    if m and os.name == "nt":
        ref = f"{m.group(1)}:/{ref[3:]}"
    path = Path(ref).expanduser()
    if path.is_absolute():
        return path.exists()
    return any((d / path).exists() for d in (base, *list(base.parents)[:3]))


def _is_path(ref: str, link: bool = False) -> bool:
    """A reference worth checking: an absolute path from a real root, or a relative
    path to a file. A folder, a git ref (`origin/develop`), an API route
    (`/guide/assignments`) or a bare name in code quotes is too ambiguous."""
    if any(c in ref for c in "*<>{}$|%") or "://" in ref or ref.startswith(("-", "@")):
        return False
    if " " in ref and not (FILE_EXT.search(ref) and ("/" in ref or "\\" in ref)):
        return False  # a command or a phrase, not a path
    if "…" in ref or "..." in ref:
        return False  # shortened on purpose
    if ":" in ref and not re.match(r"^[a-zA-Z]:[\\/]", ref):
        return False  # plugin:skill, a:b
    if re.match(r"^[a-zA-Z]:[\\/]", ref) or ref.startswith("~"):
        return True
    if ref.startswith("/"):
        return bool(GIT_BASH_DRIVE.match(ref) or UNIX_ROOT.match(ref))
    if not FILE_EXT.search(ref):
        return False
    return link or "/" in ref or "\\" in ref


def _version(name: str) -> tuple[int, ...]:
    """`20.0.0` after `9.0.0`: numbers, not text."""
    return tuple(int(x) for x in re.findall(r"\d+", name))


def _piece_file(
    kind: str, name: str, agents_dir: Path, skills_dir: Path, plugins_dir: Path
) -> Path | None:
    if kind in ("file", "rule"):
        return Path(name)
    if kind == "agent":
        own = agents_dir / f"{name}.md"
        return own if own.is_file() else None
    if ":" in name:  # a plugin skill: its newest cached version
        plugin, skill = name.split(":", 1)
        found = sorted(
            plugins_dir.glob(f"cache/*/{plugin}/*/skills/{skill}/SKILL.md"),
            key=lambda f: (_version(f.parents[2].name), str(f)),
        )
        return found[-1] if found else None
    own = skills_dir / name / "SKILL.md"
    return own if own.is_file() else None


def render(
    conn: sqlite3.Connection,
    root: Path,
    agents_dir: Path,
    skills_dir: Path,
    plugins_dir: Path,
    days: int = 30,
    rules: Rules = DEFAULT,
) -> tuple[str, str]:
    """The inventory, and every piece (section 1 uncut) for a wider scope."""
    win = _window(conn, days)
    if win is None:
        return (
            "No Claude Code requests yet: try again after a few sessions.\n",
            "",
        )
    start, end = win
    span = max(
        1.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1.0
    )
    week = 7.0 / span
    tpc = tokens_per_char(conn) or 0.25
    carry = Carry(conn, start)

    # 1. Pieces and what they cost while carried.
    cost: dict[tuple[str, str], float] = defaultdict(float)
    size: Counter[tuple[str, str]] = Counter()
    for tid, seq, kind, name, chars, ts in conn.execute(
        "SELECT thread_id, request_seq, kind, name, chars, ts FROM parts"
        " WHERE kind IN ('file', 'rule', 'skill', 'agent', 'skill_body')"
    ):
        key = ("skill" if kind == "skill_body" else kind, name)
        cost[key] += weighted(chars, carry.after(tid, seq, ts))
        size[key] = max(size[key], chars)
    uses: Counter[tuple[str, str]] = Counter()
    for tool, target in conn.execute(
        "SELECT tool, target FROM content WHERE kind = 'tool_use' AND ts >= ?"
        " AND tool IN ('Skill', 'Read')",
        (start,),
    ):
        if target:
            kind = "skill" if tool == "Skill" else "file"
            uses[(kind, str(target).lstrip("/"))] += 1
    for (name,) in conn.execute(
        "SELECT p.name FROM parts p JOIN requests r ON r.thread_id = p.thread_id"
        " AND r.seq = p.request_seq + 1"
        " WHERE p.kind = 'command' AND r.ts_first >= ?",
        (start,),
    ):
        uses[("skill", name)] += 1
    for (agent,) in conn.execute(
        "SELECT t.agent_type FROM threads t JOIN requests r ON r.thread_id ="
        " t.thread_id WHERE t.kind = 'subagent' AND r.seq = 0 AND r.ts_first >= ?",
        (start,),
    ):
        if agent:
            uses[("agent", agent)] += 1

    files: dict[tuple[str, str], Path | None] = {
        key: _piece_file(key[0], key[1], agents_dir, skills_dir, plugins_dir)
        for key in cost
    }
    ranked = sorted(cost, key=lambda k: (-cost[k], k))
    header = (
        "| kind | name | file | characters | weekly cost (input-equivalent tokens)"
        " | use |"
    )
    rows: list[str] = []
    for key in ranked:
        kind, name = key
        path = files[key]
        label = Path(name).name if kind in ("file", "rule") else name
        chars = size[key]
        if path and path.is_file():
            try:
                chars = len(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                pass
        used = _n(uses[key], "call", "calls") if kind in ("skill", "agent") else "-"
        rows.append(
            f"| {KIND[kind]} | {label} | {path or 'built-in / not found'}"
            f" | {chars:,} | {int(cost[key] * week * tpc):,} | {used} |"
        )
    all_pieces = (
        "\n".join(
            [
                "# All pieces (by weekly cost)",
                "",
                header,
                "|---|---|---|---|---|---|",
            ]
            + rows
        )
        + "\n"
    )
    lines = [
        f"# Inventory — last {int(span)} days ({start[:10]} → {end[:10]})",
        "",
        "What a script finds before the model reads. Sections 2 and 3 are sure;"
        " section 4 is candidates, the reading decides.",
        "",
        "## 1. Pieces (by weekly cost)",
        "",
        header,
        "|---|---|---|---|---|---|",
    ]
    lines += rows[:MAX_PIECES]

    rest = ranked[MAX_PIECES:]
    if rest:
        lines += [
            "",
            f"+{len(rest)} more pieces,"
            f" {int(sum(cost[k] for k in rest) * week * tpc):,} tokens a week in total;"
            " all of them are in `pieces.md`.",
        ]

    # 2-3. What a script can check for sure in the piece files.
    readable = [
        (key, path)
        for key in ranked
        if (path := files[key]) is not None and path.is_file()
    ]
    missing: list[tuple[str, str]] = []
    where: dict[str, set[str]] = defaultdict(set)
    shown: dict[str, str] = {}
    # Tools ever loaded in these transcripts: definitions sent, or names listed
    # for search. A tool loaded once is not reported (a miss, not a false alarm).
    loaded = {
        name
        for (name,) in conn.execute(
            "SELECT DISTINCT name FROM parts WHERE kind = 'deferred'"
            " UNION SELECT DISTINCT name FROM prefix WHERE source = 'tool'"
        )
    }
    unloaded: dict[str, list[str]] = defaultdict(list)
    records: list[tuple[str, int]] = []
    for _, path in readable:
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(body.splitlines(), 1):
            refs = [(r, False) for r in BACKTICK.findall(line)]
            refs += [(r, True) for r in MD_TARGET.findall(line)]
            for ref, link in refs:
                if _is_path(ref, link) and not _exists(ref, path.parent):
                    missing.append((f"{path}:{i}", ref))
            if loaded:
                for tool in TOOL_REF.findall(line):
                    if tool not in loaded:
                        unloaded[tool].append(f"{path}:{i}")
            if (
                path.name.lower() == "memory.md"
                and line.lstrip().startswith(("- ", "* "))
                and len(line) > LONG_INDEX_LINE
                and MD_TARGET.search(line)
            ):
                records.append((f"{path}:{i}", len(line)))
            for sentence in SENTENCE.split(line):
                n = _norm(sentence)
                if len(n) >= MIN_SENTENCE:
                    where[n].add(f"{path}:{i}")
                    shown.setdefault(n, sentence.strip())
    prompt = system_prompt(root)
    for line in prompt.splitlines():
        for sentence in SENTENCE.split(line):
            n = _norm(sentence)
            if len(n) >= MIN_SENTENCE and n in where:
                where[n].add("system prompt")
    lines += [
        "",
        "## 2. Stale references (sure: not on disk)",
        "",
        "Relative paths are looked up in the piece's folder and three folders up;"
        " bare file names in quotes and folder patterns are not checked.",
        "",
    ]
    if missing:
        # One row per reference: copies of a file (worktrees) repeat it.
        by_ref: dict[str, list[str]] = defaultdict(list)
        for loc, ref in sorted(set(missing)):
            by_ref[ref].append(loc)
        lines += ["| reference | first place | number of files |", "|---|---|---|"]
        refs_sorted = sorted(by_ref.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for ref, locs in refs_sorted[:MAX_STALE]:
            copies = {loc.rsplit(":", 1)[0] for loc in locs}
            lines.append(f"| `{ref}` | {locs[0]} | {len(copies)} |")
        if len(refs_sorted) > MAX_STALE:
            lines += ["", f"+{len(refs_sorted) - MAX_STALE} more references."]
    else:
        lines.append("None.")
    lines += [
        "",
        "### Tools your files name that are not loaded",
        "",
        "Named in these files, never loaded in these transcripts: the server is"
        " off, not signed in, or renamed. A line that tells Claude to use one"
        " cannot be followed; a line that only mentions it is fine.",
        "",
    ]
    if unloaded:
        lines += ["| tool | first place | places |", "|---|---|---|"]
        for tool, locs in sorted(unloaded.items())[:MAX_STALE]:
            lines.append(f"| `{tool}` | {locs[0]} | {len(locs)} |")
    else:
        lines.append("None." if loaded else "Not checked: no tool list in the window.")
    # A sentence the system prompt already says, line by line; two files that
    # share sentences, as one pair (usually a copy of the same skill).
    in_prompt = sorted(n for n, places in where.items() if "system prompt" in places)
    pair_count: Counter[tuple[str, str]] = Counter()
    pair_example: dict[tuple[str, str], str] = {}
    for n, places in sorted(where.items()):
        owners = sorted(
            {pl.rsplit(":", 1)[0] for pl in places if pl != "system prompt"}
        )
        for a_i, a in enumerate(owners):
            for b in owners[a_i + 1 :]:
                pair_count[(a, b)] += 1
                pair_example.setdefault((a, b), shown[n])
    lines += ["", "## 3. Repeated lines (sure)", ""]
    lines += ["### What the system prompt already says", ""]
    if in_prompt:
        lines += ["| line | places |", "|---|---|"]
        for n in in_prompt:
            text = shown[n][:90].replace("|", "/")
            lines.append(f"| {text} | {', '.join(sorted(where[n]))} |")
    else:
        lines.append("None.")
    lines += [
        "",
        "### Memory index lines that grew into records",
        "",
        f"`MEMORY.md` is read in every session; a line is meant as a short pointer."
        f" Lines with a link and over {LONG_INDEX_LINE} characters, longest first;"
        " their detail belongs in the linked file.",
        "",
    ]
    if records:
        ranked_records = sorted(records, key=lambda r: (-r[1], r[0]))
        lines += ["| line | characters |", "|---|---|"]
        for loc, width in ranked_records[:MAX_ROWS]:
            lines.append(f"| {loc} | {width:,} |")
        if len(records) > MAX_ROWS:
            lines += ["", f"+{len(records) - MAX_ROWS} more lines."]
    else:
        lines.append("None.")
    lines += ["", "### Files that share sentences", ""]
    if pair_count:
        lines += [
            "| file A | file B | shared sentences | example |",
            "|---|---|---|---|",
        ]
        top = sorted(pair_count.items(), key=lambda kv: (-kv[1], kv[0]))
        for (a, b), shared in top[:MAX_PAIRS]:
            example = pair_example[(a, b)][:80].replace("|", "/")
            lines.append(f"| {a} | {b} | {shared} | {example} |")
        if len(top) > MAX_PAIRS:
            lines += ["", f"+{len(top) - MAX_PAIRS} more file pairs."]
    else:
        lines.append("None.")

    # 4. What could be added.
    lines += ["", "## 4. Could be added (candidates)", ""]
    errors: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for tool, sid, ts, file in conn.execute(
        "SELECT c.tool, t.session_id, c.ts, t.file FROM content c JOIN threads t"
        " ON t.thread_id = c.thread_id WHERE c.kind = 'tool_result'"
        " AND c.is_error = 1 AND c.ts >= ? ORDER BY c.ts, t.file",
        (start,),
    ):
        errors[tool or "?"].append((sid, ts, file))
    recurring = {
        tool: got
        for tool, got in errors.items()
        if len(got) >= rules.min_recur
        and len({g[0] for g in got}) >= rules.min_sessions
        and len({g[1][:10] for g in got}) >= rules.min_days
    }
    lines += [
        "### Recurring tool errors (hook candidates)",
        "",
        "The error message is not in the ledger; read the example places, and if there"
        " is a pattern a machine can check, it becomes a hook or a permission rule.",
        "",
    ]
    if recurring:
        lines += ["| tool | errors | sessions | days | example (transcript · time) |"]
        lines += ["|---|---|---|---|---|"]
        for tool, got in sorted(recurring.items(), key=lambda kv: (-len(kv[1]), kv[0]))[
            :MAX_ROWS
        ]:
            examples = "; ".join(f"{f} · {ts}" for _, ts, f in got[-3:])
            lines.append(
                f"| {tool} | {len(got)} | {len({g[0] for g in got})}"
                f" | {len({g[1][:10] for g in got})} | {examples} |"
            )
    else:
        lines.append("None.")
    reads: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for target, sid, ts in conn.execute(
        "SELECT c.target, t.session_id, c.ts FROM content c JOIN threads t"
        " ON t.thread_id = c.thread_id WHERE c.kind = 'tool_use' AND c.tool = 'Read'"
        " AND t.kind = 'main' AND c.ts >= ?",
        (start,),
    ):
        if target:
            reads[str(target)].append((sid, ts))
    loaded = {str(p) for p in files.values() if p is not None}
    again = [
        (target, got)
        for target, got in reads.items()
        if target not in loaded
        and len({g[0] for g in got}) >= rules.min_sessions
        and len({g[1][:10] for g in got}) >= rules.min_days
    ]
    lines += [
        "",
        "### Files re-read across sessions (memory or CLAUDE.md candidates)",
        "",
    ]
    if again:
        lines += ["| file | sessions | days | reads |", "|---|---|---|---|"]
        for target, seen in sorted(
            again, key=lambda tg: (-len({g[0] for g in tg[1]}), -len(tg[1]), tg[0])
        )[:MAX_ROWS]:
            lines.append(
                f"| {target} | {len({g[0] for g in seen})}"
                f" | {len({g[1][:10] for g in seen})} | {len(seen)} |"
            )
    else:
        lines.append("None.")
    fixes = conn.execute(
        "SELECT t.file, u.ts FROM turns u JOIN threads t ON t.thread_id = u.thread_id"
        " WHERE u.correction = 1 AND t.kind = 'main' AND u.ts >= ?"
        " ORDER BY u.ts, t.file",
        (start,),
    ).fetchall()
    lines += [
        "",
        "### Corrections (rule candidates)",
        "",
        _n(len(fixes), "correction", "corrections")
        + ' (messages that start with "no", "wrong", "stop" or that say "I told'
        ' you", "why did you delete it" and the like, in English or Turkish; a'
        " rough count). Places to read:",
        "",
    ]
    lines += [f"- {file} · {ts}" for file, ts in fixes[-20:]]
    return "\n".join(lines) + "\n", all_pieces
