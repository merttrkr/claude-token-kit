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
from tokenlab.why import Carry, _window, tokens_per_char, weighted

KIND = {"file": "dosya", "rule": "kural", "skill": "skill", "agent": "ajan"}
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
            "Henüz hiç Claude Code isteği yok: birkaç oturumdan sonra tekrar dene.\n",
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
        "| tür | ad | dosya | karakter | haftalık maliyet (girdi-eşdeğeri token)"
        " | kullanım |"
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
        used = f"{uses[key]} çağrı" if kind in ("skill", "agent") else "-"
        rows.append(
            f"| {KIND[kind]} | {label} | {path or 'yerleşik / bulunamadı'}"
            f" | {chars:,} | {int(cost[key] * week * tpc):,} | {used} |"
        )
    all_pieces = (
        "\n".join(
            [
                "# Bütün parçalar (haftalık maliyete göre)",
                "",
                header,
                "|---|---|---|---|---|---|",
            ]
            + rows
        )
        + "\n"
    )
    lines = [
        f"# Envanter — son {int(span)} gün ({start[:10]} → {end[:10]})",
        "",
        "Model okumasından önce betikle çıkarılanlar. 2. ve 3. bölüm kesin;"
        " 4. bölüm aday, kararı okuma verir.",
        "",
        "## 1. Parçalar (haftalık maliyete göre)",
        "",
        header,
        "|---|---|---|---|---|---|",
    ]
    lines += rows[:MAX_PIECES]

    rest = ranked[MAX_PIECES:]
    if rest:
        lines += [
            "",
            f"+{len(rest)} parça daha, haftada toplam"
            f" {int(sum(cost[k] for k in rest) * week * tpc):,} token;"
            " tamamı `pieces.md`'de.",
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
        "## 2. Eskimiş referanslar (kesin: diskte yok)",
        "",
        "Göreli yollar parçanın klasöründe ve üç üst klasörde aranır; tırnak"
        " içindeki çıplak dosya adları ve klasör kalıpları kontrol edilmez.",
        "",
    ]
    if missing:
        # One row per reference: copies of a file (worktrees) repeat it.
        by_ref: dict[str, list[str]] = defaultdict(list)
        for loc, ref in sorted(set(missing)):
            by_ref[ref].append(loc)
        lines += ["| referans | ilk yer | dosya sayısı |", "|---|---|---|"]
        refs_sorted = sorted(by_ref.items(), key=lambda kv: (-len(kv[1]), kv[0]))
        for ref, locs in refs_sorted[:MAX_STALE]:
            copies = {loc.rsplit(":", 1)[0] for loc in locs}
            lines.append(f"| `{ref}` | {locs[0]} | {len(copies)} |")
        if len(refs_sorted) > MAX_STALE:
            lines += ["", f"+{len(refs_sorted) - MAX_STALE} referans daha."]
    else:
        lines.append("Yok.")
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
    lines += ["", "## 3. Tekrar eden satırlar (kesin)", ""]
    lines += ["### System prompt'un zaten söylediği", ""]
    if in_prompt:
        lines += ["| satır | yerler |", "|---|---|"]
        for n in in_prompt:
            text = shown[n][:90].replace("|", "/")
            lines.append(f"| {text} | {', '.join(sorted(where[n]))} |")
    else:
        lines.append("Yok.")
    lines += ["", "### Aynı cümleleri paylaşan dosyalar", ""]
    if pair_count:
        lines += ["| dosya A | dosya B | ortak cümle | örnek |", "|---|---|---|---|"]
        top = sorted(pair_count.items(), key=lambda kv: (-kv[1], kv[0]))
        for (a, b), shared in top[:MAX_PAIRS]:
            example = pair_example[(a, b)][:80].replace("|", "/")
            lines.append(f"| {a} | {b} | {shared} | {example} |")
        if len(top) > MAX_PAIRS:
            lines += ["", f"+{len(top) - MAX_PAIRS} dosya çifti daha."]
    else:
        lines.append("Yok.")

    # 4. What could be added.
    lines += ["", "## 4. Eklenebilecekler (aday)", ""]
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
        "### Tekrar eden araç hataları (hook adayı)",
        "",
        "Hata mesajı ledger'da yok; örnek yerleri oku, makinenin kontrol edebildiği"
        " bir kalıp varsa hook ya da izin kuralı olur.",
        "",
    ]
    if recurring:
        lines += ["| araç | hata | oturum | gün | örnek (transcript · zaman) |"]
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
        lines.append("Yok.")
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
        "### Oturumlar arasında tekrar okunan dosyalar (hafıza ya da CLAUDE.md adayı)",
        "",
    ]
    if again:
        lines += ["| dosya | oturum | gün | okuma |", "|---|---|---|---|"]
        for target, seen in sorted(
            again, key=lambda tg: (-len({g[0] for g in tg[1]}), -len(tg[1]), tg[0])
        )[:MAX_ROWS]:
            lines.append(
                f"| {target} | {len({g[0] for g in seen})}"
                f" | {len({g[1][:10] for g in seen})} | {len(seen)} |"
            )
    else:
        lines.append("Yok.")
    fixes = conn.execute(
        "SELECT t.file, u.ts FROM turns u JOIN threads t ON t.thread_id = u.thread_id"
        " WHERE u.correction = 1 AND t.kind = 'main' AND u.ts >= ?"
        " ORDER BY u.ts, t.file",
        (start,),
    ).fetchall()
    lines += [
        "",
        "### Düzeltmeler (kural adayı)",
        "",
        f'{len(fixes)} düzeltme ("hayır", "yanlış", "dur", "no", "wrong" gibi'
        " başlayan mesajlar; kaba bir sayım). Okunacak yerler:",
        "",
    ]
    lines += [f"- {file} · {ts}" for file, ts in fixes[-20:]]
    return "\n".join(lines) + "\n", all_pieces
