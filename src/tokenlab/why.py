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
}
MD_LINK = re.compile(r"\]\(([^)\s]+\.md)\)")
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


def render(conn: sqlite3.Connection, days: int = 30, rules: Rules = DEFAULT) -> str:
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
    agent_calls = Counter(
        a
        for (a,) in conn.execute(
            "SELECT t.agent_type FROM threads t WHERE t.kind = 'subagent'"
            " AND EXISTS (SELECT 1 FROM requests r WHERE r.thread_id = t.thread_id"
            " AND r.ts_first >= ?)",
            (start,),
        )
        if a
    )

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
            "sunucuyu kapat ya da araçları izin listesinden çıkar",
        )

    # S / A: listing lines for skills and agents that were never used.
    for kind, calls, label, fix_own, fix_plugin in (
        (
            "skill",
            skill_calls,
            "skill",
            "`skillOverrides` ile gizle",
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
            who = f"`{owner}` plugin'inin" if owner else "senin"
            add(
                "S" if kind == "skill" else "A",
                raw[owner],
                cost[owner],
                f"{who} {len(names)} {label}'i listede ama {days} günde hiç"
                " kullanılmadı",
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

    # R: large tool results and files read again and again.
    big: dict[str, list[str]] = defaultdict(list)
    big_raw: Counter[str] = Counter()
    big_cost: dict[str, float] = defaultdict(float)
    for tid, seq, tool, chars, ts in conn.execute(
        "SELECT thread_id, request_seq, tool, chars, ts FROM content"
        " WHERE kind = 'tool_result' AND chars >= ? AND ts >= ?",
        (rules.big_result_chars, start),
    ):
        n = carry.after(tid, seq, ts)
        if not n:
            continue
        big[tool or "?"].append(f"{tid[:12]}:{seq}")
        big_raw[tool or "?"] += chars * n
        big_cost[tool or "?"] += weighted(chars, n)
    for tool, where in big.items():
        add(
            "R",
            big_raw[tool],
            big_cost[tool],
            f"`{tool}` {len(where)} kez {rules.big_result_chars:,}+ karakter"
            " döndürdü; sonraki her istekte taşındı",
            _names(sorted(where), 4),
            "çıktıyı daralt (Read'e limit, grep'e desen, komutta | head)",
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
        again[f"{tid[:12]}:{Path(target).name}"] = len(got)
        for seq, ts, chars in got[1:]:
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

    findings.sort(key=lambda f: (-f.cost, f.code, f.text))
    total_carried = sum(by_cat.values())
    total_weighted = sum(wcat.values())
    paid = conn.execute(
        "SELECT COALESCE(SUM(input + cache_read * ? + cache_write_5m * ?"
        " + (cache_write - cache_write_5m) * ?), 0) FROM requests WHERE ts_first >= ?",
        (READ, WRITE_5M, WRITE_1H, start),
    ).fetchone()[0]
    lines = [
        f"# Token haritası — son {int(span)} gün ({start[:10]} → {end[:10]})",
        "",
        f"{sessions} oturum · {n_req:,} istek · istemde {prompt:,} token,"
        f" çıktıda {output:,} token",
        "1 karakter ≈ "
        + (
            f"{tpc:.3f} token (bu veriden ölçüldü)"
            if ratio
            else "0.25 token (varsayım: ölçecek ilk istek yok)"
        ),
        "Maliyet = girdi-eşdeğeri token: önbellekten okuma 0.1, önbelleğe yazma 1.25"
        " (5 dk) / 2 (1 sa); Claude Code'un fiyat oranları, Opus'ta okuma 0.05"
        " (yaklaşık). Abonelikte kotaya etkisi ölçülmedi.",
        "",
        "## 1. Her istekte tekrar giden kısım",
        "",
        "| parça | istek başına (tahmini token) | token payı | maliyet payı |",
        "|---|---|---|---|",
    ]

    def pct(part: float, whole: float) -> str:
        return f"{part / whole:.0%}" if whole else "-"

    for cat, chars in sorted(wcat.items(), key=lambda kv: (-kv[1], kv[0])):
        per = tok(by_cat[cat] / max(1, n_req))
        lines.append(
            f"| {cat} | {per:,} | {pct(tok(by_cat[cat]), prompt)}"
            f" | {pct(tok(chars), paid)} |"
        )
    lines.append(
        f"| **toplam** | **{tok(total_carried / max(1, n_req)):,}** |"
        f" **{pct(tok(total_carried), prompt)}** |"
        f" **{pct(tok(total_weighted), paid)}** |"
    )
    lines += [
        "",
        "## 2. Olmaması gerekenler (haftalık maliyete göre, büyükten küçüğe)",
        "",
    ]
    if findings:
        lines += [
            "| | haftalık maliyet | haftalık ham token | bulgu | kanıt | ne yapılır |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {f.code} | {f.cost:,} | {f.weekly:,} | {f.text} | {f.evidence}"
            f" | {f.fix} |"
            for f in findings
        ]
    else:
        lines.append("Eşiği geçen bulgu yok.")
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
    files = sorted(
        ((n, c) for (k, n), c in wpart.items() if k in ("file", "rule", "skill_body")),
        key=lambda kv: (-kv[1], kv[0]),
    )[:6]
    lines += [
        "",
        "## 3. Görünmeyen",
        "",
        "Oturum başlığı, compaction özeti ve öneri istekleri transcript'e yazılmaz;"
        " bu rapor onları göremez.",
        "",
        "## 4. İsteğe bağlı: Claude okuması (model işi)",
        "",
        "En çok taşınan dosyalar. İstersen Claude bunları şu sorularla okur:"
        " davranışı değiştiriyor mu, doğru okuyucuya mı gidiyor, doğru yerde mi,"
        " Claude'un kendi varsayılanını mı tekrar ediyor?",
        "",
    ]
    lines += [f"- {name} — haftalık maliyet {tok(c * week):,}" for name, c in files]
    return "\n".join(lines) + "\n"
