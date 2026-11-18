"""Attribution report v1 over the OTel era.

OTel records every API request with its real cost, so the report covers only requests
OTel saw. Each one is joined to its transcript request by request id for the thread,
thinking and tool content; an OTel request with no transcript twin is hidden (titles,
summaries, hook prompts) and is reported by its query_source. The output is a pure
function of the ledger.
"""

import sqlite3
from datetime import datetime

from tokenlab.prices import effective_rates

PER_TOKEN = 1e-6
TOP = 10
# A request misses the cache when it reads this much less than the previous request's
# whole prompt (the plan's baseline heuristic).
MISS_SLACK = 5000

_ERA = """
CREATE TEMP TABLE IF NOT EXISTS era AS
SELECT o.ts, o.model, o.query_source, COALESCE(o.effort, r.effort) AS effort,
       o.speed, o.input, o.output,
       o.cache_read, o.cache_write, o.cache_write_5m, o.cache_write_1h, o.cost_usd,
       o.duration_ms, r.thread_id, r.seq AS rseq, COALESCE(r.thinking, 0) AS thinking,
       CASE WHEN r.thread_id IS NULL THEN 'hidden: ' || COALESCE(o.query_source, '?')
            WHEN t.kind = 'main' THEN 'main'
            ELSE COALESCE(t.agent_type, '?') END AS label
FROM otel_requests o
LEFT JOIN (SELECT request_id, MIN(thread_id) AS thread_id FROM requests
           GROUP BY request_id) m ON m.request_id = o.request_id
LEFT JOIN requests r ON r.thread_id = m.thread_id AND r.request_id = m.request_id
LEFT JOIN threads t ON t.thread_id = m.thread_id
"""


def ensure_era(conn: sqlite3.Connection) -> None:
    """Build the connection-local `era` table: OTel requests joined to transcripts."""
    conn.execute(_ERA)
    conn.execute("CREATE INDEX IF NOT EXISTS temp.era_thread ON era (thread_id, rseq)")


def by_thread(conn: sqlite3.Connection) -> list[tuple[str, int, int, int, int, float]]:
    """(thread, requests, output, cache_read, cache_write, OTel USD)."""
    ensure_era(conn)
    return conn.execute(
        "SELECT label, COUNT(*), SUM(output), SUM(cache_read), SUM(cache_write),"
        " SUM(cost_usd) FROM era GROUP BY label ORDER BY 6 DESC, label"
    ).fetchall()


def by_mechanism(conn: sqlite3.Connection) -> list[tuple[str, int, float]]:
    """(mechanism, tokens, USD at catalog prices)."""
    ensure_era(conn)
    names = (
        "cache read",
        "cache write 5m",
        "cache write 1h",
        "input (uncached)",
        "output: visible",
        "output: thinking",
        "output: hidden requests",
    )
    tokens = dict.fromkeys(names, 0)
    usd = dict.fromkeys(names, 0.0)
    for model, speed, inp, out, rd, w5, w1, think, hidden in conn.execute(
        "SELECT model, speed, input, output, cache_read, cache_write_5m,"
        " cache_write_1h, thinking, thread_id IS NULL FROM era"
    ).fetchall():
        r = effective_rates(model, inp + rd + w5 + w1, speed == "fast")
        if r is None:
            continue
        parts = [
            ("cache read", rd, r.cache_read),
            ("cache write 5m", w5, r.cache_write_5m),
            ("cache write 1h", w1, r.cache_write_1h),
            ("input (uncached)", inp, r.input),
        ]
        if hidden:
            parts.append(("output: hidden requests", out, r.output))
        else:
            parts.append(("output: visible", out - think, r.output))
            parts.append(("output: thinking", think, r.output))
        for name, n, rate in parts:
            tokens[name] += n
            usd[name] += n * rate * PER_TOKEN
    return sorted(((n, tokens[n], usd[n]) for n in names), key=lambda x: (-x[2], x[0]))


def by_model_effort(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, int, int, int, float]]:
    """(model, effort, requests, output, thinking, OTel USD)."""
    ensure_era(conn)
    return conn.execute(
        "SELECT model, COALESCE(effort, '-'), COUNT(*), SUM(output), SUM(thinking),"
        " SUM(cost_usd) FROM era GROUP BY 1, 2 ORDER BY 6 DESC, 1, 2"
    ).fetchall()


def cold_starts(conn: sqlite3.Connection) -> list[tuple[str, int, int, float]]:
    """A thread's first request, if OTel saw it: (thread, starts, cache_write, USD)."""
    ensure_era(conn)
    return conn.execute(
        "SELECT e.label, COUNT(*), SUM(e.cache_write), SUM(e.cost_usd) FROM era e"
        " WHERE e.rseq = (SELECT MIN(seq) FROM requests r"
        " WHERE r.thread_id = e.thread_id)"
        " GROUP BY 1 ORDER BY 4 DESC, 1"
    ).fetchall()


def rewrite_cause(gap_s: float, model_switch: bool, compacted: bool) -> str:
    if compacted:
        return "compaction"
    if model_switch:
        return "model switch"
    if gap_s > 3600:
        return "idle > 1h"
    if gap_s > 300:
        return "idle > 5m"
    return "prefix change"


def _when(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def rewrites(conn: sqlite3.Connection) -> list[tuple[str, int, int, float]]:
    """Cache misses in a thread, by cause: (cause, misses, tokens rewritten, USD)."""
    ensure_era(conn)
    out: dict[str, tuple[int, int, float]] = {}
    # previous request in the same thread: (thread, ts, model, prompt, finished at)
    prev: tuple[str, str, str, int, float] | None = None
    for thread, ts, dur, model, speed, inp, rd, wr, w5, w1 in conn.execute(
        "SELECT thread_id, ts, duration_ms, model, speed, input, cache_read,"
        " cache_write, cache_write_5m, cache_write_1h FROM era"
        " WHERE thread_id IS NOT NULL ORDER BY thread_id, rseq"
    ).fetchall():
        end = _when(ts)
        if prev is not None and prev[0] == thread and rd < prev[3] - MISS_SLACK:
            (compacted,) = conn.execute(
                "SELECT COUNT(*) FROM events WHERE thread_id=?"
                " AND subtype='compact_boundary' AND ts>? AND ts<?",
                (thread, prev[1], ts),
            ).fetchone()
            gap = end - dur / 1000 - prev[4]
            cause = rewrite_cause(gap, model != prev[2], compacted > 0)
            r = effective_rates(model, inp + rd + w5 + w1, speed == "fast")
            usd = 0.0
            if r is not None:
                usd = (w5 * r.cache_write_5m + w1 * r.cache_write_1h) * PER_TOKEN
            n, toks, cost = out.get(cause, (0, 0, 0.0))
            out[cause] = (n + 1, toks + wr, cost + usd)
        prev = (thread, ts, model, inp + rd + wr, end)
    return sorted(
        ((c, n, toks, cost) for c, (n, toks, cost) in out.items()),
        key=lambda x: (-x[3], x[0]),
    )


_ERA_CONTENT = (
    "FROM content c JOIN (SELECT DISTINCT thread_id, label FROM era"
    " WHERE thread_id IS NOT NULL) t ON t.thread_id = c.thread_id"
    " WHERE EXISTS (SELECT 1 FROM era e WHERE e.thread_id = c.thread_id"
    " AND e.rseq = c.request_seq)"
)


def reread_files(conn: sqlite3.Connection) -> list[tuple[str, int, int, int]]:
    """Files read in the era: (path, reads, result chars, threads)."""
    ensure_era(conn)
    return conn.execute(
        "SELECT u.target, COUNT(*), SUM(COALESCE(res.chars, 0)),"  # noqa: S608
        " COUNT(DISTINCT u.thread_id)"
        f" FROM (SELECT c.* {_ERA_CONTENT}) u"
        " LEFT JOIN content res ON res.thread_id = u.thread_id"
        " AND res.tool_use_id = u.tool_use_id AND res.kind = 'tool_result'"
        " WHERE u.kind = 'tool_use' AND u.tool = 'Read' AND u.target IS NOT NULL"
        " GROUP BY 1 ORDER BY 2 DESC, 3 DESC, 1 LIMIT ?",
        (TOP,),
    ).fetchall()


def char_turns(
    conn: sqlite3.Connection,
) -> list[tuple[str, str, str, int, int, int]]:
    """Tool results by chars x later requests in the thread: (thread, tool, target,
    chars, requests after, char-turns)."""
    ensure_era(conn)
    return conn.execute(
        "SELECT t.label, COALESCE(c.tool, '?'), COALESCE(c.target, ''), c.chars,"  # noqa: S608
        " (SELECT COUNT(*) FROM requests r WHERE r.thread_id = c.thread_id"
        "  AND r.seq > c.request_seq) AS later,"
        " c.chars * (SELECT COUNT(*) FROM requests r WHERE r.thread_id = c.thread_id"
        "  AND r.seq > c.request_seq) AS turns"
        f" {_ERA_CONTENT} AND c.kind = 'tool_result'"
        " ORDER BY turns DESC, c.chars DESC, 1, 2, 3 LIMIT ?",
        (TOP,),
    ).fetchall()


def busiest_spawns(conn: sqlite3.Connection) -> list[tuple[str, str, int, float]]:
    """(agent type, description, requests, OTel USD) for subagent threads."""
    ensure_era(conn)
    return conn.execute(
        "SELECT e.label, COALESCE(t.description, ''), COUNT(*), SUM(e.cost_usd)"
        " FROM era e JOIN threads t ON t.thread_id = e.thread_id"
        " WHERE t.kind = 'subagent' GROUP BY e.thread_id"
        " ORDER BY 3 DESC, 4 DESC, e.thread_id LIMIT ?",
        (TOP,),
    ).fetchall()


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:5.1f}%" if whole else "    -"


def _cut(s: str, n: int) -> str:
    return s if len(s) <= n else "..." + s[-(n - 3) :]


def render(conn: sqlite3.Connection) -> str:
    (n,) = conn.execute("SELECT COUNT(*) FROM otel_requests").fetchone()
    if not n:
        return "no OTel requests in the ledger\n"
    ensure_era(conn)
    first, last, sessions, total = conn.execute(
        "SELECT MIN(ts), MAX(ts), COUNT(DISTINCT session_id), SUM(cost_usd)"
        " FROM otel_requests"
    ).fetchone()
    lines = [
        f"tokenlab report v1: OTel era {first} .. {last}",
        f"requests {n:,}  sessions {sessions}  cost USD {total:,.4f} (OTel cost_usd)",
        "",
        "by thread",
        f"  {'thread':34} {'n':>6} {'output':>11} {'cache_read':>14}"
        f" {'cache_write':>12} {'USD':>11} {'share':>6}",
    ]
    for label, k, out, rd, wr, usd in by_thread(conn):
        lines.append(
            f"  {_cut(label, 34):34} {k:>6,} {out:>11,} {rd:>14,} {wr:>12,}"
            f" {usd:>11,.4f} {_pct(usd, total)}"
        )
    mech = by_mechanism(conn)
    priced = sum(usd for _, _, usd in mech)
    lines += ["", "by mechanism (catalog prices)"]
    lines += [
        f"  {name:34} {toks:>14,} {usd:>11,.4f} {_pct(usd, priced)}"
        for name, toks, usd in mech
    ]
    lines.append(f"  catalog-priced {priced:,.4f} vs OTel {total:,.4f}")
    lines += [
        "",
        "by model and effort",
        f"  {'model':26} {'effort':8} {'n':>6} {'output':>11} {'thinking':>11}"
        f" {'USD':>11}",
    ]
    lines += [
        f"  {m:26} {e:8} {k:>6,} {out:>11,} {th:>11,} {usd:>11,.4f}"
        for m, e, k, out, th, usd in by_model_effort(conn)
    ]
    lines += ["", "cold starts (first request of a thread)"]
    lines += [
        f"  {_cut(label, 34):34} {k:>6,} {wr:>14,} {usd:>11,.4f}"
        for label, k, wr, usd in cold_starts(conn)
    ]
    rw = rewrites(conn)
    lines += ["", f"cache rewrites (read < previous prompt - {MISS_SLACK:,})"]
    lines += [
        f"  {cause:34} {k:>6,} {toks:>14,} {usd:>11,.4f}" for cause, k, toks, usd in rw
    ]
    if not rw:
        lines.append("  none")
    lines += ["", "most read files"]
    lines += [
        f"  {_cut(path, 70):70} reads {k:>3} chars {ch:>10,} threads {th}"
        for path, k, ch, th in reread_files(conn)
    ]
    lines += ["", "longest-lived tool results (chars x later requests)"]
    lines += [
        f"  {_cut(label, 20):20} {_cut(tool, 24):24} {_cut(target, 40):40}"
        f" {ch:>9,} x {later:>4} = {turns:>13,}"
        for label, tool, target, ch, later, turns in char_turns(conn)
    ]
    lines += ["", "busiest spawns"]
    lines += [
        f"  {_cut(label, 24):24} {_cut(desc, 50):50} {k:>5} {usd:>9,.4f}"
        for label, desc, k, usd in busiest_spawns(conn)
    ]
    return "\n".join(lines) + "\n"
