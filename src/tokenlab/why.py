"""Token map: where every prompt token goes, and which ones should not be there.

Read-only and deterministic: the same ledger, rules and files on disk give the same
report. A part's cost is its size times the requests that carry it. An attachment
(CLAUDE.md, a skill or agent listing line, a tool result) rides in every later
request of its thread until the next compaction; a tool definition rides in every
request until the next request shape.

Tokens are estimated from characters with a ratio measured on this data (each main
session's first request: prompt tokens / characters of what it carried).
"""

import re
import sqlite3
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from tokenlab.agents import (
    GENERIC,
    NO_TRACE,
    AgentDef,
    Fit,
    Unit,
    fit,
    route,
    window,
)
from tokenlab.rules import DEFAULT, Rules

INF = 1 << 62
CATEGORY = {
    "file": "CLAUDE.md ve hafıza dosyaları",
    "rule": "klasör kuralları (okunan dosyayla yüklenen)",
    "skill": "skill listesi",
    "agent": "ajan listesi",
    "mcp": "MCP talimatları",
    "deferred": "ertelenen araç adları",
    "skill_body": "çağrılan skill gövdeleri",
    "hook": "hook metni",
}
# Every angle the report looks at, in display order for ties. A finding's code is
# its angle; D (CLAUDE.md) and H (hooks) are measured but never called waste.
ANGLE = {
    "R": "Araç çıktıları",
    "M": "Hafıza",
    "D": "CLAUDE.md ve kurallar",
    "T": "Araçlar ve MCP",
    "A": "Ajanlar",
    "S": "Skill'ler",
    "H": "Hook'lar",
    "C": "Cache",
}
# ToolSearch: denying it loads every deferred tool in full. The placeholder is
# Claude Code's own stand-in for deferred tools, not a tool anyone calls.
NEVER_FLAG = frozenset({"ToolSearch", "DeferredToolPlaceholder"})
ATTACHMENT = {
    "file": "instructions",
    "rule": "nested_memory",
    "skill": "skill_listing",
    "agent": "agent_listing_delta",
    "mcp": "mcp_instructions_delta",
    "deferred": "deferred_tools_delta",
    "skill_body": "invoked_skills",
    "hook": "hook_additional_context",
}
MD_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")
# An MCP tool that changes the outside system rather than reading it.
WRITE_TOOL = re.compile(
    r"(?:^|_)(?:create|update|delete|remove|transition|add|edit|post|put|patch"
    r"|send|set|write|upload|merge|move|assign|link)(?:_|$)",
    re.IGNORECASE,
)
# Claude Code's own price ratios to plain input: a cache read 0.1, a 5-minute cache
# write 1.25, a 1-hour write 2. Opus 5.5 reads at 0.05, so the weights are approximate.
READ, WRITE_5M, WRITE_1H = 0.1, 1.25, 2.0


def weighted(chars: float, n: int) -> float:
    """Input-equivalent cost of a part carried by n requests: written to the cache
    once, then read from it."""
    return chars * (WRITE_5M + READ * (n - 1)) if n > 0 else 0.0


@dataclass(frozen=True)
class Finding:
    code: str
    cost: int  # weighted (input-equivalent) tokens per week: the sort key
    weekly: int  # raw estimated tokens per week
    text: str
    evidence: str
    fix: str


class Carry:
    """How many in-window requests carry something placed at a thread position."""

    def __init__(self, conn: sqlite3.Connection, start: str) -> None:
        self.reqs: dict[str, list[int]] = defaultdict(list)
        ts_by: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for tid, seq, ts in conn.execute(
            "SELECT thread_id, seq, ts_first FROM requests ORDER BY thread_id, seq"
        ):
            ts_by[tid].append((ts, seq))
            if ts >= start:
                self.reqs[tid].append(seq)
        # A compaction: when it happened and the first request after it.
        self.cuts: dict[str, list[tuple[str, int]]] = defaultdict(list)
        for tid, ts in conn.execute(
            "SELECT thread_id, ts FROM events WHERE subtype = 'compact_boundary'"
            " ORDER BY thread_id, ts"
        ):
            after = [s for t, s in ts_by.get(tid, []) if t > ts]
            if after:
                self.cuts[tid].append((ts, min(after)))
        self.shapes: dict[str, list[int]] = defaultdict(list)
        for tid, seq in conn.execute(
            "SELECT DISTINCT thread_id, request_seq FROM prefix"
            " WHERE source IN ('system', 'tool') ORDER BY thread_id, request_seq"
        ):
            self.shapes[tid].append(seq)

    def after(self, tid: str, seq: int, ts: str | None) -> int:
        """Requests after `seq` up to the next compaction (attachments, results).

        A part re-sent after a compaction shares `seq` with the request before the
        compaction, so its own time decides which compaction comes next."""
        cuts = self.cuts.get(tid, [])
        later = [s for t, s in cuts if (t > ts if ts else s > seq)]
        end = min(later) if later else INF
        seqs = self.reqs.get(tid, [])
        return bisect_left(seqs, end) - bisect_right(seqs, seq)

    def shape(self, tid: str, seq: int) -> int:
        """Requests from `seq` up to the next request shape (system, tools)."""
        shapes = self.shapes.get(tid, [])
        i = bisect_right(shapes, seq)
        end = shapes[i] if i < len(shapes) else INF
        seqs = self.reqs.get(tid, [])
        return bisect_left(seqs, end) - bisect_left(seqs, seq)


def _window(conn: sqlite3.Connection, days: int) -> tuple[str, str] | None:
    end = conn.execute("SELECT MAX(ts_first) FROM requests").fetchone()[0]
    if end is None:
        return None
    start = datetime.fromisoformat(end) - timedelta(days=days)
    first = conn.execute("SELECT MIN(ts_first) FROM requests").fetchone()[0]
    return max(first, start.isoformat().replace("+00:00", "Z")), end


def tokens_per_char(conn: sqlite3.Connection) -> float | None:
    """Median over main sessions of first-request prompt tokens per carried char."""
    ratios = [
        float(tok) / float(chars)
        for tok, chars in conn.execute(
            """
            SELECT r.input + r.cache_read + r.cache_write,
                   (SELECT COALESCE(SUM(p.chars), 0) FROM prefix p
                    WHERE p.thread_id = t.thread_id AND p.request_seq IN (-1, 0))
                 + (SELECT COALESCE(SUM(u.chars), 0) FROM turns u
                    WHERE u.thread_id = t.thread_id AND u.request_seq = -1)
            FROM threads t JOIN requests r ON r.thread_id = t.thread_id AND r.seq = 0
            WHERE t.kind = 'main' AND EXISTS (SELECT 1 FROM prefix p
                WHERE p.thread_id = t.thread_id AND p.request_seq = 0
                AND p.source = 'tool')
            """
        )
        if tok and chars
    ]
    return median(ratios) if ratios else None


def _norm(path: str) -> str:
    return path.replace("\\", "/").lower()


def _owner(name: str) -> str:
    return name.split(":", 1)[0] if ":" in name else ""


def _names(xs: list[str], cap: int = 8) -> str:
    shown = ", ".join(xs[:cap])
    return shown + (f" … +{len(xs) - cap}" if len(xs) > cap else "")


def _human(n: float) -> str:
    """11,252,735,713 → '11,3 milyar': a size anyone can read."""
    for size, word in ((1e9, "milyar"), (1e6, "milyon")):
        if n >= size:
            return f"{n / size:.1f}".replace(".", ",") + f" {word}"
    return f"{n / 1e3:.0f} bin" if n >= 1e4 else f"{int(n):,}"


def _share(part: float, whole: float) -> str:
    if not whole or part <= 0:
        return "-"
    return "<%1" if part / whole < 0.01 else f"%{part / whole * 100:.0f}"


def render(
    conn: sqlite3.Connection,
    days: int = 30,
    rules: Rules = DEFAULT,
    catalog: dict[str, AgentDef] | None = None,
) -> str:
    win = _window(conn, days)
    if win is None:
        return "Henüz hiç Claude Code isteği yok: birkaç oturumdan sonra tekrar dene.\n"
    start, end = win
    span = max(
        1.0, (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days + 1.0
    )
    week = 7.0 / span
    ratio = tokens_per_char(conn)
    tpc = ratio or 0.25
    carry = Carry(conn, start)

    def tok(chars: float) -> int:
        return int(chars * tpc)

    n_req, prompt, output = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(input + cache_read + cache_write), 0),"
        " COALESCE(SUM(output), 0) FROM requests WHERE ts_first >= ?",
        (start,),
    ).fetchone()
    sessions = conn.execute(
        "SELECT COUNT(DISTINCT t.session_id) FROM threads t JOIN requests r"
        " ON r.thread_id = t.thread_id WHERE t.kind = 'main' AND r.ts_first >= ?",
        (start,),
    ).fetchone()[0]

    # Carried characters per category and per (kind, name).
    by_cat: dict[str, float] = defaultdict(float)
    by_part: Counter[tuple[str, str]] = Counter()
    wcat: dict[str, float] = defaultdict(float)
    wpart: dict[tuple[str, str], float] = defaultdict(float)
    # A part is its own text; the rendered attachment adds a little wrapper text.
    # Category totals scale parts up by the rendered/parts ratio of their kind.
    rendered = dict(
        conn.execute(
            "SELECT name, SUM(chars) FROM prefix WHERE source = 'attachment'"
            " GROUP BY name"
        ).fetchall()
    )
    own = dict(conn.execute("SELECT kind, SUM(chars) FROM parts GROUP BY kind"))
    scale = {
        kind: max(1.0, (rendered.get(att) or 0) / own[kind]) if own.get(kind) else 1.0
        for kind, att in ATTACHMENT.items()
    }
    for tid, seq, source, name, chars in conn.execute(
        "SELECT thread_id, request_seq, source, name, chars FROM prefix"
        " WHERE source IN ('system', 'tool')"
    ):
        k = carry.shape(tid, seq)
        n, w = k * chars, weighted(chars, k)
        if source == "system":
            cat = "sistem prompt'u"
        else:
            mcp = name.startswith("mcp__")
            cat = "araç tanımları (MCP)" if mcp else "araç tanımları"
            by_part[("tool", name)] += n
            wpart[("tool", name)] += w
        by_cat[cat] += n
        wcat[cat] += w
    for tid, seq, kind, name, chars, ts in conn.execute(
        "SELECT thread_id, request_seq, kind, name, chars, ts FROM parts"
        " WHERE kind != 'command'"
    ):
        k = carry.after(tid, seq, ts)
        by_cat[CATEGORY[kind]] += k * chars * scale[kind]
        by_part[(kind, name)] += k * chars
        wcat[CATEGORY[kind]] += weighted(chars, k) * scale[kind]
        wpart[(kind, name)] += weighted(chars, k)

    # What was used in the window.
    used_tools: Counter[str] = Counter()
    skill_calls: Counter[str] = Counter()
    reads: Counter[str] = Counter()
    for name, target in conn.execute(
        "SELECT tool, target FROM content WHERE kind = 'tool_use' AND ts >= ?",
        (start,),
    ):
        used_tools[name] += 1
        if name == "Skill" and target:
            skill_calls[str(target).lstrip("/")] += 1
        if name == "Read" and target:
            reads[_norm(str(target))] += 1
    for (name,) in conn.execute(
        "SELECT p.name FROM parts p JOIN requests r ON r.thread_id = p.thread_id"
        " AND r.seq = p.request_seq + 1"
        " WHERE p.kind = 'command' AND r.ts_first >= ?",
        (start,),
    ):
        skill_calls[name] += 1
    # Each subagent call: its type and when it started.
    agent_runs = [
        (a, ts)
        for a, ts in conn.execute(
            "SELECT t.agent_type, MIN(r.ts_first) FROM threads t JOIN requests r"
            " ON r.thread_id = t.thread_id WHERE t.kind = 'subagent'"
            " GROUP BY t.thread_id HAVING MIN(r.ts_first) >= ?",
            (start,),
        )
        if a
    ]
    agent_calls = Counter(a for a, _ in agent_runs)

    # An item absent from the last sessions is already fixed: say so, don't propose it.
    recent = (
        (datetime.fromisoformat(end) - timedelta(days=rules.current_days))
        .isoformat()
        .replace("+00:00", "Z")
    )
    present: set[tuple[str, str]] = set()
    for kind, name in conn.execute(
        "SELECT DISTINCT p.kind, p.name FROM parts p WHERE EXISTS"
        " (SELECT 1 FROM requests r WHERE r.thread_id = p.thread_id"
        " AND r.seq > p.request_seq AND r.ts_first >= ?)",
        (recent,),
    ):
        present.add((kind, name))
    for (name,) in conn.execute(
        "SELECT DISTINCT p.name FROM prefix p WHERE p.source = 'tool' AND EXISTS"
        " (SELECT 1 FROM requests r WHERE r.thread_id = p.thread_id"
        " AND r.seq >= p.request_seq AND r.ts_first >= ?)",
        (recent,),
    ):
        present.add(("tool", name))
    fixed = 0

    def current(kind: str, name: str) -> bool:
        nonlocal fixed
        if (kind, name) in present:
            return True
        fixed += 1
        return False

    findings: list[Finding] = []

    def add(code: str, raw: float, cost: float, text: str, ev: str, fix: str) -> None:
        if tok(cost * week) >= rules.min_weekly_tokens:
            findings.append(
                Finding(code, tok(cost * week), tok(raw * week), text, ev, fix)
            )

    # T: tool definitions never called.
    mcp_unused: dict[str, list[str]] = defaultdict(list)
    mcp_raw: Counter[str] = Counter()
    mcp_cost: dict[str, float] = defaultdict(float)
    for (kind, name), chars in by_part.items():
        if kind != "tool" or used_tools[name] or name in NEVER_FLAG:
            continue
        if not current(kind, name):
            continue
        if name.startswith("mcp__"):
            server = name.split("__")[1]
            mcp_unused[server].append(name.split("__", 2)[-1])
            mcp_raw[server] += chars
            mcp_cost[server] += wpart[(kind, name)]
        else:
            add(
                "T",
                chars,
                wpart[(kind, name)],
                f"`{name}` aracı {days} günde hiç çağrılmadı",
                "her istekte tanımı gidiyor",
                "kullanmıyorsan `permissions.deny`'a ekle",
            )
    for server, tools in mcp_unused.items():
        add(
            "T",
            mcp_raw[server],
            mcp_cost[server],
            f"MCP `{server}`: {len(tools)} araç hiç çağrılmadı",
            _names(sorted(tools)),
            "`permissions.deny`'a ekle (`mcp__<sunucu>__<araç>`) ya da sunucuyu kapat",
        )

    # S / A: listing lines for skills and agents that were never used.
    for kind, calls, label, fix_own, fix_plugin in (
        (
            "skill",
            skill_calls,
            "skill",
            "`skillOverrides`: `user-invocable-only` (/ad ile çağrılır) ya da `off`",
            "plugin skill'i: `skillOverrides` işlemez; SKILL.md'ye"
            " `disable-model-invocation: true`",
        ),
        (
            "agent",
            agent_calls,
            "ajan",
            "kendi ajanınsa dosyasını kaldır; yerleşikse `permissions.deny`'a"
            " `Agent(<ad>)` ekle",
            "`permissions.deny`'a `Agent(<ad>)` ekle",
        ),
    ):
        groups: dict[str, list[str]] = defaultdict(list)
        raw: Counter[str] = Counter()
        cost: dict[str, float] = defaultdict(float)
        for (pk, name), chars in by_part.items():
            if pk == kind and not calls[name] and current(pk, name):
                groups[_owner(name)].append(name)
                raw[_owner(name)] += chars
                cost[_owner(name)] += wpart[(pk, name)]
        for owner, names in groups.items():
            # No owner prefix: the user's own or built in; the listing can't tell.
            who = (
                f"`{owner}` plugin'inin {len(names)} {label}'i"
                if owner
                else f"{len(names)} {label}"
            )
            add(
                "S" if kind == "skill" else "A",
                raw[owner],
                cost[owner],
                f"{who} listede ama {days} günde hiç kullanılmadı",
                _names(sorted(names)),
                fix_plugin if owner else fix_own,
            )

    bodies = {n: c for (k, n), c in wpart.items() if k == "skill_body" and c}
    if bodies:
        ranked = sorted(bodies, key=lambda n: (-bodies[n], n))
        add(
            "S",
            sum(c for (k, _), c in by_part.items() if k == "skill_body"),
            sum(bodies.values()),
            f"{len(bodies)} skill'in gövdesi compaction'dan sonra yeniden eklendi"
            " ve taşındı",
            _names(ranked, 5),
            "büyük skill'i küçült: ayrıntıyı ayrı referans dosyalarına taşı",
        )

    # M: memory index lines whose file was never read.
    for (kind, name), chars in sorted(by_part.items()):
        if kind != "file" or not _norm(name).endswith("/memory.md"):
            continue
        path = Path(name)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        total = max(1, sum(len(x) + 1 for x in lines))
        unread: tuple[int, list[str]] = (0, [])
        missing: tuple[int, list[str]] = (0, [])
        for line in lines:
            links = MD_LINK.findall(line)
            if not links:
                continue
            if any(not (path.parent / f).exists() for f in links):
                missing = (missing[0] + len(line) + 1, missing[1] + links)
            elif not any(reads[_norm(str(path.parent / f))] for f in links):
                unread = (unread[0] + len(line) + 1, unread[1] + links)
        for (dead_chars, dead), text, fix in (
            (
                unread,
                f"hafıza index'inde {len(unread[1])} satırın dosyası {days} günde"
                " Read ile hiç okunmadı",
                "arşivle ya da tek satırlık bir özet altında topla",
            ),
            (
                missing,
                f"hafıza index'inde {len(missing[1])} satırın dosyası yok",
                "satırı sil ya da yolu düzelt",
            ),
        ):
            if dead:
                add(
                    "M",
                    chars * dead_chars / total,
                    wpart[(kind, name)] * dead_chars / total,
                    text,
                    f"{path}: {_names(sorted(dead), 5)}",
                    fix,
                )
        # Memory files written in the window but never read back.
        folder = _norm(str(path.parent)) + "/"
        written: dict[str, float] = defaultdict(float)
        for tid, seq, ts, target, wchars in conn.execute(
            "SELECT thread_id, request_seq, ts, target, chars FROM content"
            " WHERE kind = 'tool_use' AND tool IN ('Write', 'Edit') AND ts >= ?",
            (start,),
        ):
            norm = _norm(str(target or ""))
            if norm.startswith(folder) and not reads[norm]:
                written[norm] += weighted(wchars, carry.after(tid, seq, ts))
        if written:
            add(
                "M",
                sum(written.values()),
                sum(written.values()),
                f"{len(written)} hafıza dosyası yazıldı ama {days} günde Read ile"
                " hiç okunmadı",
                _names(sorted(Path(w).name for w in written), 5),
                "gerçekten gerekiyor mu bak; gerekmiyorsa yazma kuralını daralt",
            )

    # A: an outside system read by the main session itself, again and again. An
    # agent msg_reads it and returns a summary, so the results leave the main context.
    # Errors and writes (create, update, transition, ...) are not reading work.
    by_system: dict[str, list[tuple[str, str, int, str, str, int, str]]] = defaultdict(
        list
    )
    for tid, tuid, seq, ts, tool, chars, sid in conn.execute(
        "SELECT c.thread_id, c.tool_use_id, c.request_seq, c.ts, c.tool, c.chars,"
        " t.session_id FROM content c JOIN threads t ON t.thread_id = c.thread_id"
        " WHERE c.kind = 'tool_result' AND t.kind = 'main' AND c.is_error = 0"
        " AND substr(c.tool, 1, 5) = 'mcp__' AND c.ts >= ?",
        (start,),
    ):
        if WRITE_TOOL.search(tool.split("__", 2)[-1]):
            continue
        server = tool.split("__")[1]
        system = rules.external.get(server, server)
        by_system[system].append((tid, tuid, seq, ts, tool, chars, sid))
    # Jobs measured against the agent standard: (job, where it runs, fit).
    candidates: list[tuple[str, str, Fit]] = []
    moved: set[tuple[str, str]] = set()
    # When each user message arrived in each main thread, in request order.
    prompts: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for tid, seq, ts in conn.execute(
        "SELECT thread_id, request_seq, ts FROM turns"
        " WHERE kind IN ('human', 'sdk', 'scheduled')"
        " ORDER BY thread_id, request_seq, ts"
    ):
        prompts[tid].append((seq, ts))
    for system, sys_reads in sorted(by_system.items()):
        # One unit per user message: an agent would do that message's reading and
        # return one summary, so its msg_reads count together.
        per_turn: dict[tuple[str, int], list[tuple[int, str, int, str, str]]] = (
            defaultdict(list)
        )
        for tid, _, seq, ts, tool, chars, sid in sys_reads:
            turn = bisect_right(prompts.get(tid, []), (seq, ts))
            per_turn[(tid, turn)].append((seq, ts, chars, tool, sid))
        # What an agent would keep out of the main context: per message, what it
        # read beyond a summary, for as long as main would have carried it.
        saving = 0.0
        for (tid, _), msg_reads in per_turn.items():
            last_seq, last_ts = max((r[0], r[1]) for r in msg_reads)
            excess = sum(r[2] for r in msg_reads) - rules.agent_read_chars
            if excess > 0:
                saving += weighted(excess, carry.after(tid, last_seq, last_ts))
        sys_fit = fit(
            [
                Unit(
                    msg_reads[0][4],
                    min(r[1] for r in msg_reads)[:10],
                    sum(r[2] for r in msg_reads),
                    # One server's read tools are one allowlist: a fixed scope.
                    frozenset({system}),
                )
                for msg_reads in per_turn.values()
            ],
            tok(saving * week),
        )
        verdict = sys_fit.verdict(rules)
        if verdict == "tekrar etmiyor":
            continue
        candidates.append((f"`{system}` okuma", "ana oturum", sys_fit))
        if verdict.startswith("değmez"):
            continue
        sys_raw, sys_cost = 0.0, 0.0
        sys_tools: Counter[str] = Counter()
        for tid, _, seq, ts, tool, chars, _ in sys_reads:
            n = carry.after(tid, seq, ts)
            sys_raw += chars * n
            sys_cost += weighted(chars, n)
            sys_tools[tool] += 1
        # Existing agents granted the tools behind most of these msg_reads.
        able = sorted(
            a.name
            for a in (catalog or {}).values()
            if a.tools
            and sum(n for t, n in sys_tools.items() if t in a.tools)
            >= rules.scope_share * len(sys_reads)
        )
        before = len(findings)
        add(
            "A",
            sys_raw,
            sys_cost,
            f"ana oturum `{system}` sistemini {len(sys_reads)} kez kendisi okudu"
            f" ({sys_fit.sessions} oturum); sonuçlar sonraki her istekte taşındı",
            "en çok: "
            + _names(
                [
                    f"{t.split('__', 2)[-1]} ×{c}"
                    for t, c in sorted(sys_tools.items(), key=lambda x: (-x[1], x[0]))
                ],
                3,
            ),
            (
                f"{_names([f'`{a}`' for a in able], 3)} bu sistemi zaten okuyabiliyor:"
                " ana oturum işi ona versin diye açıklamasını keskinleştir"
                if able
                else f"`{system}` okuyup kısa özet döndüren bir ajan yaz"
            ),
        )
        if len(findings) > before:
            moved |= {(g[0], g[1]) for g in sys_reads}
    # Recurring general-purpose jobs no existing agent covers.
    in_window, longer = window(conn, days, rules, end)
    for sig, jobs in sorted(route(in_window, longer, catalog or {})[1].items()):
        if sig == NO_TRACE:  # what it read or wrote is not visible
            continue
        gp_fit = fit([Unit(c.session, c.day, c.read_chars, c.tools) for c in jobs])
        if gp_fit.verdict(rules) != "tekrar etmiyor":
            candidates.append((sig, "general-purpose", gp_fit))

    # R: large tool results and files read again and again.
    big: Counter[str] = Counter()
    big_raw: Counter[str] = Counter()
    big_cost: dict[str, float] = defaultdict(float)
    biggest: Counter[str] = Counter()
    # Which files cost the most: the evidence a user can act on.
    big_files: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for tid, tuid, seq, tool, chars, ts, target in conn.execute(
        "SELECT thread_id, tool_use_id, request_seq, tool, chars, ts, target"
        " FROM content WHERE kind = 'tool_result' AND chars >= ? AND ts >= ?",
        (rules.big_result_chars, start),
    ):
        n = carry.after(tid, seq, ts)
        if not n or (tid, tuid) in moved:  # an agent candidate above counts it
            continue
        tool = tool or "?"
        big[tool] += 1
        big_raw[tool] += chars * n
        big_cost[tool] += weighted(chars, n)
        biggest[tool] = max(biggest[tool], chars)
        if tool == "Read" and target:
            big_files[tool][Path(str(target)).name] += weighted(chars, n)
    for tool, count in big.items():
        files = big_files.get(tool, {})
        add(
            "R",
            big_raw[tool],
            big_cost[tool],
            f"`{tool}` {count} kez {rules.big_result_chars:,}+ karakter"
            " döndürdü; sonraki her istekte taşındı",
            "en pahalı: " + _names(sorted(files, key=lambda f: (-files[f], f)), 3)
            if files
            else f"en büyüğü {biggest[tool]:,} karakter",
            "çıktıyı daralt: Read'e limit/offset, Grep'e desen, komut çıktısını kısalt",
        )
    results: dict[tuple[str, str], list[tuple[int, str, int]]] = defaultdict(list)
    for tid, target, seq, ts, chars in conn.execute(
        "SELECT u.thread_id, u.target, r.request_seq, r.ts, r.chars FROM content u"
        " JOIN content r ON r.thread_id = u.thread_id"
        " AND r.tool_use_id = u.tool_use_id AND r.kind = 'tool_result'"
        " WHERE u.kind = 'tool_use' AND u.tool = 'Read' AND u.ts >= ?"
        " ORDER BY u.thread_id, u.target, r.request_seq, r.ts",
        (start,),
    ):
        results[(tid, str(target))].append((seq, ts, chars))
    again = Counter[str]()
    again_raw, again_cost = 0.0, 0.0
    for (tid, target), got in sorted(results.items()):
        if len(got) < rules.reread_min:
            continue
        # A big result is already a big-result finding: count it there only.
        small = [
            (seq, ts, chars)
            for seq, ts, chars in got[1:]
            if chars < rules.big_result_chars
        ]
        if not small:
            continue
        again[f"{tid[:12]}:{Path(target).name}"] = len(got)
        for seq, ts, chars in small:
            n = carry.after(tid, seq, ts)
            again_raw += chars * n
            again_cost += weighted(chars, n)
    if again:
        top = [f"{k} ×{v}" for k, v in sorted(again.items(), key=lambda kv: -kv[1])]
        add(
            "R",
            again_raw,
            again_cost,
            f"{len(again)} dosya aynı oturumda {rules.reread_min}+ kez okundu",
            _names(top, 4),
            "bir kez oku, gerekirse sadece değişen aralığı oku",
        )

    # C: cold cache after an idle gap longer than the cache TTL (measured tokens).
    cold, cold_raw, cold_cost = 0, 0, 0.0
    prev: dict[str, datetime] = {}
    for tid, ts, ttl, w5, w1, cw in conn.execute(
        "SELECT thread_id, ts_first, ttl_class, cache_write_5m, cache_write_1h,"
        " cache_write FROM requests ORDER BY thread_id, seq"
    ):
        w1 += max(0, cw - w5 - w1)
        now = datetime.fromisoformat(ts)
        last = prev.get(tid)
        prev[tid] = now
        limit = 3600 if ttl == "1h" else 300
        if (
            ts >= start
            and last is not None
            and (now - last).total_seconds() > limit
            and w5 + w1 >= rules.cold_write_tokens
        ):
            cold += 1
            cold_raw += w5 + w1
            cold_cost += w5 * WRITE_5M + w1 * WRITE_1H
    if cold:
        add(
            "C",
            cold_raw / tpc,
            cold_cost / tpc,
            f"{cold} kez cache soğudu: boşluktan sonra bağlam yeniden yazıldı",
            "token sayısı ölçülen (tahmin değil)",
            "uzun aradan sonra /compact ya da yeni oturum; uzun işleri böl",
        )

    # E: repeated failures.
    errors = Counter(
        tool
        for (tool,) in conn.execute(
            "SELECT tool FROM content WHERE kind = 'tool_result' AND is_error = 1"
            " AND ts >= ?",
            (start,),
        )
    )
    truncated = conn.execute(
        "SELECT COUNT(*) FROM prefix p JOIN requests r ON r.thread_id = p.thread_id"
        " AND r.seq = p.request_seq + 1 WHERE p.source = 'attachment'"
        " AND p.name = 'read_truncation_notice' AND r.ts_first >= ?",
        (start,),
    ).fetchone()[0]
    hooks = conn.execute(
        "SELECT COUNT(*) FROM content WHERE kind = 'tool_result'"
        " AND decision LIKE 'hook%' AND ts >= ?",
        (start,),
    ).fetchone()[0]

    # H: text hooks add to the context, carried like any attachment.
    hook_raw = sum(c for (k, _), c in by_part.items() if k == "hook")
    hook_cost = sum(c for (k, _), c in wpart.items() if k == "hook")

    paid = conn.execute(
        "SELECT COALESCE(SUM(input + cache_read * ? + cache_write_5m * ?"
        " + (cache_write - cache_write_5m) * ?), 0) FROM requests WHERE ts_first >= ?",
        (READ, WRITE_5M, WRITE_1H, start),
    ).fetchone()[0]
    paid_week = paid * week

    # Cache cooling is measured but not a fix anyone can be sure of: an observation.
    cache = [f for f in findings if f.code == "C"]
    waste = sorted(
        (f for f in findings if f.code != "C"), key=lambda f: (-f.cost, f.code, f.text)
    )
    total_carried = sum(by_cat.values())
    total_weighted = sum(wcat.values())
    per_request = tok(total_carried / max(1, n_req))

    # One line per angle: what was found, its share of the weekly share, what to do.
    rows: list[tuple[float, str, str, str, str]] = []
    for code, angle in ANGLE.items():
        mine = [f for f in waste if f.code == code]
        share = float(sum(f.cost for f in mine))
        found, todo = "", ""
        if mine:
            found = mine[0].text + (f" (+{len(mine) - 1} bulgu)" if mine[1:] else "")
            todo = mine[0].fix
        if code == "D":
            docs = sorted(
                (
                    (n, c)
                    for (k, n), c in wpart.items()
                    if (k == "file" and not _norm(n).endswith("/memory.md"))
                    or k == "rule"
                ),
                key=lambda kv: (-kv[1], kv[0]),
            )
            share = tok(sum(c for _, c in docs) * week)
            if docs:
                name, c = docs[0]
                found = (
                    f"{len(docs)} dosya; en pahalısı {Path(name).name}"
                    f" ({_share(tok(c * week), paid_week)})"
                )
                todo = "gereksiz satırları Claude'a buldur (4. bölüm)"
        elif code == "A":
            total = sum(agent_calls.values())
            generic = sum(agent_calls[g] for g in GENERIC)
            if total:
                head = (
                    f"general-purpose payı %{generic / total * 100:.0f}"
                    f" ({generic}/{total} çağrı)"
                )
                # The last days too: a change made mid-week shows there first,
                # and the latest share decides the advice.
                latest = generic / total
                for back in (7, rules.current_days) if span >= 14 else ():
                    since = (
                        (datetime.fromisoformat(end) - timedelta(days=back))
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                    runs = [a for a, ts in agent_runs if ts >= since]
                    if runs:
                        g = sum(a in GENERIC for a in runs)
                        latest = g / len(runs)
                        head += f", son {back} gün %{latest * 100:.0f}"
                found = head + ("; " + found if mine else "")
                if latest >= 0.2:
                    todo = "tekrar eden işleri isimli bir ajana taşı (5. bölüm)"
            ready = [c for c in candidates if c[2].verdict(rules) == "ajan adayı"]
            if ready:
                found = (found + "; " if found else "") + (
                    f"{len(ready)} iş ölçülen ajan koşullarını geçiyor"
                )
                todo = "2. bölüm sonundaki ajan adaylarını yaz (`agent.md`)"
        elif code == "H":
            share = tok(hook_cost * week)
            if hook_raw or hooks:
                found = (
                    f"hook metni haftada {_human(tok(hook_raw * week))} token taşındı;"
                    f" {hooks} çağrı engellendi"
                )
                if share / max(1.0, paid_week) >= 0.01:
                    todo = "hook'un her seferinde eklediği metin gerekli mi bak"
        elif code == "C" and cache:
            share = cache[0].cost
            found = cache[0].text + " (gözlem)"
            todo = "uzun aradan sonra yeni oturum aç; kesin bir düzeltme değil"
        rows.append((share, angle, found or "sorun yok", todo or "-", code))

    cut = sum(f.cost for f in waste)
    lines = [
        f"# Token haritası — son {int(span)} gün ({start[:10]} → {end[:10]})",
        "",
        f"**Her istekte aynı kısım tekrar gidiyor: istek başına"
        f" ~{_human(per_request)} token, gönderilenin"
        f" {_share(tok(total_carried), prompt)} kadarı.**",
        (
            f"**Kesilebilir israf: haftalık maliyetin ~{_share(cut, paid_week)}"
            f" kadarı, {len(waste)} madde. En büyüğü"
            f" {ANGLE[waste[0].code].lower()}.**"
            if waste
            else "**Eşiği geçen kesilebilir israf yok.**"
        ),
        "",
        f"{sessions} oturum · {n_req:,} istek · Claude'a {_human(prompt)} token"
        f" gitti, {_human(output)} token geldi.",
        "",
        "## Özet",
        "",
        "| açı | ne bulundu | maliyetteki payı | ne yapılır |",
        "|---|---|---|---|",
    ]
    order = list(ANGLE)
    for share, angle, found, todo, _ in sorted(
        rows, key=lambda r: (-r[0], order.index(r[4]))
    ):
        lines.append(f"| {angle} | {found} | {_share(share, paid_week)} | {todo} |")
    lines += ["", "## İlk adım", ""]
    if waste:
        first = waste[0]
        lines.append(
            f"{ANGLE[first.code]}: {first.text}. Yapılacak: {first.fix}."
            f" Maliyetteki payı ~{_share(first.cost, paid_week)}."
        )
    else:
        lines.append("Kesilebilir bulgu yok; 4. bölümdeki dosyaları okutmayı dene.")
    lines += [
        "",
        "## 1. Tekrar tekrar giden sabit kısım",
        "",
        "| parça | istek başına (tahmini token) | token payı | maliyet payı |",
        "|---|---|---|---|",
    ]
    for cat, chars in sorted(wcat.items(), key=lambda kv: (-kv[1], kv[0])):
        per = tok(by_cat[cat] / max(1, n_req))
        lines.append(
            f"| {cat} | {per:,} | {_share(tok(by_cat[cat]), prompt)}"
            f" | {_share(tok(chars), paid)} |"
        )
    lines.append(
        f"| **toplam** | **{per_request:,}** |"
        f" **{_share(tok(total_carried), prompt)}** |"
        f" **{_share(tok(total_weighted), paid)}** |"
    )
    lines += [
        "",
        "## 2. Olmaması gerekenler (maliyete göre, büyükten küçüğe)",
        "",
    ]
    if waste:
        lines += [
            "| açı | maliyetteki payı | haftalık token (girdi-eşdeğeri) | bulgu"
            " | kanıt | ne yapılır |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {ANGLE[f.code]} | {_share(f.cost, paid_week)} | {f.cost:,} | {f.text}"
            f" | {f.evidence} | {f.fix} |"
            for f in waste
        ]
    else:
        lines.append("Eşiği geçen bulgu yok.")
    if candidates:
        rank = {"ajan adayı": 0}
        lines += [
            "",
            "### Ajan adayları (ajan standardına göre)",
            "",
            f"Tekrar eden = {rules.min_recur}+ çağrı, {rules.min_sessions}+"
            f" oturum, {rules.min_days}+ gün. Birim: ajan çağrısı; ana oturumda bir"
            " kullanıcı mesajı için yapılan okumalar. Çok okuyan = birim başına"
            f" {rules.agent_read_chars:,}+ karakter (bir ajan özetinden büyük)."
            f" Sabit kapsam = çağrıların %{rules.scope_share * 100:.0f}+'ı"
            " işin çekirdek araçlarıyla yetiniyor (ana oturum işinde tek sunucu tek"
            " izin listesidir, hep sabit sayılır; hükmü okunan boyut verir). Modelin"
            " işi yönetmesi"
            " gerekip gerekmediği ölçülmez: `references/agent.md`.",
            "",
            "| iş (okuduğu → yazdığı) | nerede | birim | oturum | gün"
            " | okunan (medyan karakter) | haftalık kazanç (tahmini token)"
            " | çekirdek araçlarla | hüküm |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        ranked_jobs = sorted(
            candidates,
            key=lambda c: (rank.get(c[2].verdict(rules), 1), -c[2].calls, c[1], c[0]),
        )
        for job, where, jf in ranked_jobs[: rules.max_candidates]:
            lines.append(
                f"| {job} | {where} | {jf.calls} | {jf.sessions} | {jf.days}"
                f" | {jf.read_chars:,}"
                f" | {f'{jf.saving:,}' if jf.saving is not None else '-'}"
                f" | %{jf.scope * 100:.0f}"
                f" | {jf.verdict(rules)} |"
            )
        hidden = ranked_jobs[rules.max_candidates :]
        if hidden:
            passing = sum(c[2].verdict(rules) == "ajan adayı" for c in hidden)
            lines += [
                "",
                f"+{len(hidden)} iş daha"
                + (
                    f", {passing} tanesi ajan adayı."
                    if passing
                    else "; hiçbiri koşulları geçmiyor."
                ),
            ]
    lines += [
        "",
        "Maliyet = girdi-eşdeğeri token: önbellekten okunan token 0.1, önbelleğe"
        " yazılan 1.25 (5 dk) ya da 2 (1 sa) sayılır; Claude Code'un fiyat oranları."
        " Abonelikte kotaya etkisi ölçülmedi. 1 karakter ≈ "
        + (
            f"{tpc:.3f} token (bu veriden ölçüldü)."
            if ratio
            else "0.25 token (varsayım: ölçecek ilk istek yok)."
        ),
    ]
    for f in cache:
        lines += ["", f"Gözlem: {f.text}; haftada {f.cost:,} girdi-eşdeğeri token."]
    if fixed:
        lines += [
            "",
            f"{fixed} öğe son {rules.current_days} günün oturumlarında yok:"
            " zaten düzelmiş görünüyor, listelenmedi.",
        ]
    if errors or hooks or truncated:
        top_errors = sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        err_text = ", ".join(f"{t} {n}" for t, n in top_errors) or "-"
        lines += [
            "",
            f"Araç hataları: {err_text} · hook engeli: {hooks}"
            f" · kırpılan okuma: {truncated}",
        ]
    carried = sorted(
        ((n, c) for (k, n), c in wpart.items() if k in ("file", "rule", "skill_body")),
        key=lambda kv: (-kv[1], kv[0]),
    )[:6]
    lines += [
        "",
        "## 3. Görünmeyen",
        "",
        "- Oturum başlığı, compaction özeti ve öneri istekleri transcript'e yazılmaz;"
        " bu rapor onları göremez.",
        "- Token'ın ne kazandırdığı (iş bitti mi, sonuç doğru muydu) kayıtlarda yok."
        " Rapor maliyeti ve kullanılmayanı görür, değeri değil.",
        "",
        "## 4. İsteğe bağlı: Claude okuması (model işi)",
        "",
        "En çok taşınan dosyalar ve skill gövdeleri. İstersen Claude bunları şu"
        " sorularla okur: davranışı değiştiriyor mu, doğru okuyucuya mı gidiyor,"
        " doğru yerde mi, Claude'un kendi varsayılanını mı tekrar ediyor?",
        "",
    ]
    lines += [
        f"- {name} — maliyetteki payı {_share(tok(c * week), paid_week)}"
        for name, c in carried
    ]
    return "\n".join(lines) + "\n"
