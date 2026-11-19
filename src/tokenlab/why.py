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

from tokenlab import reconcile
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
    "file": "CLAUDE.md and memory files",
    "rule": "folder rules (loaded with the file read)",
    "skill": "skill list",
    "agent": "agent list",
    "mcp": "MCP instructions",
    "deferred": "deferred tool names",
    "skill_body": "invoked skill bodies",
    "hook": "hook text",
}
# Every angle the report looks at, in display order for ties. A finding's code is
# its angle; D (CLAUDE.md) and H (hooks) are measured but never called waste.
ANGLE = {
    "R": "Tool outputs",
    "M": "Memory",
    "D": "CLAUDE.md and rules",
    "T": "Tools and MCP",
    "A": "Agents",
    "S": "Skills",
    "H": "Hooks",
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
    """11,252,735,713 → '11.3 billion': a size anyone can read."""
    for size, word in ((1e9, "billion"), (1e6, "million")):
        if n >= size:
            return f"{n / size:.1f} {word}"
    return f"{n / 1e3:.0f} thousand" if n >= 1e4 else f"{int(n):,}"


def _n(count: int, one: str, many: str) -> str:
    """`1 tool was`, `3 tools were`: a count with the right English form."""
    return f"{count:,} {one if count == 1 else many}"


def _health(conn: sqlite3.Connection) -> tuple[str, str]:
    """(a warning for the top or "", a line for "Not visible") from what ingest
    could not read. A ledger built before the health table gives ("", "")."""
    try:
        got = conn.execute("SELECT what, name, n FROM health").fetchall()
    except sqlite3.OperationalError:
        return "", ""
    count: Counter[str] = Counter()
    unknown: list[str] = []
    for what, name, n in got:
        count[what] += n
        if what == "unknown":
            unknown.append(name)
    records, lost = count["records"], count["broken"] + count["no_usage"]
    if not records:
        return "", ""
    line = (
        f"- Records read: {records:,}. Skipped:"
        f" {_n(count['broken'], 'cut-off line', 'cut-off lines')},"
        f" {_n(count['no_usage'], 'answer', 'answers')} without token counts"
    )
    if unknown:
        line += (
            f", {_n(count['unknown'], 'record', 'records')} of types this version"
            f" does not know ({_names(sorted(unknown), 5)}; usually harmless)"
        )
    warn = ""
    if count["unknown"] / records > 0.01:
        warn = (
            f"**Warning: {_share(count['unknown'], records)} of the records are of"
            " types this version does not know; if Claude Code changed its format,"
            " the numbers may be low. Update token-lab; if this stays, open an"
            " issue.**"
        )
    if lost / records > 0.01:
        warn = (
            f"**Warning: {_share(lost, records)} of the records could not be read;"
            " the numbers may be low. Update token-lab; if this stays, open an"
            " issue.**"
        )
    return warn, line + "."


def _coverage(conn: sqlite3.Connection, start: str, end: str) -> str:
    """How much of Claude Code's own cost counter the transcripts hold."""

    def ms(iso: str) -> float:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000

    # Processes that overlap the window: started before its end, ran into it.
    rs = [
        r for r in reconcile.rows(conn) if r.start_time <= ms(end) and r.asof >= start
    ]
    _, _, held, counted = reconcile.totals(rs)
    if not counted:
        return (
            "- Claude Code wrote no cost counter (`cost-state`) in this window, so"
            " how much the transcripts miss was not checked."
        )
    procs = len({(r.session_id, r.start_time) for r in rs})
    return (
        "- Claude Code's own cost counter (`cost-state`,"
        f" {_n(procs, 'process', 'processes')}):"
        f" the transcripts hold {_share(held, counted)} of its cost. The rest never"
        " reached a transcript (background requests such as titles and suggestions,"
        " compaction; the split is not measured). Measured over whole processes"
        " that overlap the window, so the report's totals are likely low by"
        " about that share."
    )


def _share(part: float, whole: float) -> str:
    if not whole or part <= 0:
        return "-"
    return "<1%" if part / whole < 0.01 else f"{part / whole * 100:.0f}%"


def render(
    conn: sqlite3.Connection,
    days: int = 30,
    rules: Rules = DEFAULT,
    catalog: dict[str, AgentDef] | None = None,
) -> str:
    win = _window(conn, days)
    if win is None:
        return "No Claude Code requests yet: try again after a few sessions.\n"
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
            cat = "system prompt"
        else:
            mcp = name.startswith("mcp__")
            cat = "tool definitions (MCP)" if mcp else "tool definitions"
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
                f"tool `{name}` was never called in {days} days",
                "its definition is sent with every request",
                "if you do not use it, add it to `permissions.deny`",
            )
    for server, tools in mcp_unused.items():
        add(
            "T",
            mcp_raw[server],
            mcp_cost[server],
            f"MCP `{server}`: {_n(len(tools), 'tool was', 'tools were')} never called",
            _names(sorted(tools)),
            "add to `permissions.deny` (`mcp__<server>__<tool>`) or turn the server"
            " off",
        )

    # S / A: listing lines for skills and agents that were never used.
    for kind, calls, label, fix_own, fix_plugin in (
        (
            "skill",
            skill_calls,
            "skill",
            "`skillOverrides`: `user-invocable-only` (called with /name) or `off`",
            "plugin skill: `skillOverrides` does not work; add to SKILL.md"
            " `disable-model-invocation: true`",
        ),
        (
            "agent",
            agent_calls,
            "agent",
            "if it is your own agent, remove its file; if built in, add"
            " `Agent(<name>)` to `permissions.deny`",
            "add `Agent(<name>)` to `permissions.deny`",
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
            many = f"{len(names)} {label}{'s' if len(names) != 1 else ''}"
            who = f"plugin `{owner}`: {many}" if owner else many
            add(
                "S" if kind == "skill" else "A",
                raw[owner],
                cost[owner],
                f"{who} listed but never used in {days} days",
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
            f"{_n(len(bodies), 'skill body was', 'skill bodies were')} added again"
            " after compaction and carried",
            _names(ranked, 5),
            "shrink the big skill: move detail into separate reference files",
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
                _n(
                    len(unread[1]),
                    "memory index line points",
                    "memory index lines point",
                )
                + f" to files never read with Read in {days} days",
                "archive them or group them under a one-line summary",
            ),
            (
                missing,
                _n(
                    len(missing[1]),
                    "memory index line points",
                    "memory index lines point",
                )
                + " to missing files",
                "delete the line or fix the path",
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
                f"{_n(len(written), 'memory file was', 'memory files were')}"
                " written but never read with Read"
                f" in {days} days",
                _names(sorted(Path(w).name for w in written), 5),
                "check if it is really needed; if not, narrow the rule that writes it",
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
        if verdict == "not recurring":
            continue
        candidates.append((f"`{system}` reading", "main session", sys_fit))
        if verdict.startswith("not worth it"):
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
            f"the main session read system `{system}` itself"
            f" {_n(len(sys_reads), 'time', 'times')}"
            f" ({_n(sys_fit.sessions, 'session', 'sessions')});"
            " results were carried in every later request",
            "most: "
            + _names(
                [
                    f"{t.split('__', 2)[-1]} ×{c}"
                    for t, c in sorted(sys_tools.items(), key=lambda x: (-x[1], x[0]))
                ],
                3,
            ),
            (
                f"{_names([f'`{a}`' for a in able], 3)} can already read this system:"
                " sharpen its description so the main session hands the job over"
                if able
                else f"write an agent that reads `{system}` and returns a short summary"
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
        if gp_fit.verdict(rules) != "not recurring":
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
            f"`{tool}` ×{count} returned {rules.big_result_chars:,}+ characters;"
            " carried in every later request",
            "most expensive: " + _names(sorted(files, key=lambda f: (-files[f], f)), 3)
            if files
            else f"largest {biggest[tool]:,} characters",
            "narrow the output: limit/offset for Read, a pattern for Grep,"
            " shorter command output",
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
            f"{_n(len(again), 'file was', 'files were')} read"
            f" {rules.reread_min}+ times in one session",
            _names(top, 4),
            "read once; if needed, read only the changed range",
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
            f"cache went cold {cold} time{'s' if cold != 1 else ''}:"
            " context was rewritten after a gap",
            "token count is measured (not estimated)",
            "after a long break use /compact or a new session; split long jobs",
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
            found = mine[0].text + (f" (+{len(mine) - 1} findings)" if mine[1:] else "")
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
                    f"{_n(len(docs), 'file', 'files')};"
                    f" most expensive {Path(name).name}"
                    f" ({_share(tok(c * week), paid_week)})"
                )
                todo = "have Claude find the unneeded lines (section 4)"
        elif code == "A":
            total = sum(agent_calls.values())
            generic = sum(agent_calls[g] for g in GENERIC)
            if total:
                head = (
                    f"general-purpose share {generic / total * 100:.0f}%"
                    f" ({generic}/{total} calls)"
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
                        head += f", last {back} days {latest * 100:.0f}%"
                found = head + ("; " + found if mine else "")
                if latest >= 0.2:
                    todo = "move the recurring jobs to a named agent (section 5)"
            ready = [c for c in candidates if c[2].verdict(rules) == "agent candidate"]
            if ready:
                found = (found + "; " if found else "") + (
                    _n(len(ready), "job meets", "jobs meet")
                    + " the measured agent conditions"
                )
                todo = "write the agent candidates at the end of section 2 (`agent.md`)"
        elif code == "H":
            share = tok(hook_cost * week)
            if hook_raw or hooks:
                found = (
                    f"hook text carried {_human(tok(hook_raw * week))} tokens a week;"
                    f" {_n(hooks, 'call', 'calls')} blocked"
                )
                if share / max(1.0, paid_week) >= 0.01:
                    todo = "check if the text the hook adds every time is needed"
        elif code == "C" and cache:
            share = cache[0].cost
            found = cache[0].text + " (observation)"
            todo = "open a new session after a long break; not a sure fix"
        rows.append((share, angle, found or "no issue", todo or "-", code))

    cut = sum(f.cost for f in waste)
    warn, health = _health(conn)
    lines = [
        f"# Token map — last {int(span)} days ({start[:10]} → {end[:10]})",
        "",
        f"**The same fixed part is sent again with every request: about"
        f" {_human(per_request)} tokens per request,"
        f" {_share(tok(total_carried), prompt)} of what is sent.**",
        (
            f"**Cuttable waste: about {_share(cut, paid_week)} of weekly cost,"
            f" {_n(len(waste), 'item', 'items')}. The largest is"
            f" {ANGLE[waste[0].code].lower()}.**"
            if waste
            else "**No cuttable waste above the threshold.**"
        ),
        *([warn] if warn else []),
        "",
        f"{_n(sessions, 'session', 'sessions')}"
        f" · {_n(n_req, 'request', 'requests')} · {_human(prompt)} tokens"
        f" went to Claude, {_human(output)} tokens came back.",
        "",
        "## Summary",
        "",
        "| angle | what was found | share of cost | what to do |",
        "|---|---|---|---|",
    ]
    order = list(ANGLE)
    for share, angle, found, todo, _ in sorted(
        rows, key=lambda r: (-r[0], order.index(r[4]))
    ):
        lines.append(f"| {angle} | {found} | {_share(share, paid_week)} | {todo} |")
    lines += ["", "## First step", ""]
    if waste:
        first = waste[0]
        lines.append(
            f"{ANGLE[first.code]}: {first.text}. To do: {first.fix}."
            f" Share of cost ~{_share(first.cost, paid_week)}."
        )
    else:
        lines.append(
            "Nothing cuttable found; try having Claude read the files in section 4."
        )
    lines += [
        "",
        "## 1. The fixed part sent again and again",
        "",
        "| part | per request (estimated tokens) | token share | cost share |",
        "|---|---|---|---|",
    ]
    for cat, chars in sorted(wcat.items(), key=lambda kv: (-kv[1], kv[0])):
        per = tok(by_cat[cat] / max(1, n_req))
        lines.append(
            f"| {cat} | {per:,} | {_share(tok(by_cat[cat]), prompt)}"
            f" | {_share(tok(chars), paid)} |"
        )
    lines.append(
        f"| **total** | **{per_request:,}** |"
        f" **{_share(tok(total_carried), prompt)}** |"
        f" **{_share(tok(total_weighted), paid)}** |"
    )
    lines += [
        "",
        "## 2. What should not be there (by cost, largest first)",
        "",
    ]
    if waste:
        lines += [
            "| angle | share of cost | weekly tokens (input-equivalent) | finding"
            " | evidence | what to do |",
            "|---|---|---|---|---|---|",
        ]
        lines += [
            f"| {ANGLE[f.code]} | {_share(f.cost, paid_week)} | {f.cost:,} | {f.text}"
            f" | {f.evidence} | {f.fix} |"
            for f in waste
        ]
    else:
        lines.append("No finding above the threshold.")
    if candidates:
        rank = {"agent candidate": 0}
        lines += [
            "",
            "### Agent candidates (by the agent standard)",
            "",
            f"Recurring = {rules.min_recur}+ calls, {rules.min_sessions}+"
            f" sessions, {rules.min_days}+ days. Unit: an agent call; in the main"
            " session, the reads made for one user message. Heavy reader = per unit"
            f" {rules.agent_read_chars:,}+ characters (bigger than an agent summary)."
            f" Fixed scope = {rules.scope_share * 100:.0f}%+ of the calls"
            " get by with the core tools of the job (in main-session work one server"
            " is one allowlist, always counted as fixed; the read size decides the"
            " verdict). Whether the model has to steer the job"
            " is not measured: `references/agent.md`.",
            "",
            "| job (reads → writes) | where | unit | sessions | days"
            " | read (median characters) | weekly saving (estimated tokens)"
            " | with core tools | verdict |",
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
                f" | {jf.scope * 100:.0f}%"
                f" | {jf.verdict(rules)} |"
            )
        hidden = ranked_jobs[rules.max_candidates :]
        if hidden:
            passing = sum(c[2].verdict(rules) == "agent candidate" for c in hidden)
            lines += [
                "",
                f"+{_n(len(hidden), 'more job', 'more jobs')}"
                + (
                    f", {passing} of them"
                    + (" an agent candidate." if passing == 1 else " agent candidates.")
                    if passing
                    else "; none meet the conditions."
                ),
            ]
    lines += [
        "",
        "Cost = input-equivalent tokens: a token read from the cache counts 0.1, a"
        " token written to the cache 1.25 (5 min) or 2 (1 h); Claude Code's price"
        " ratios. The effect on a subscription quota is not measured. 1 character ≈ "
        + (
            f"{tpc:.3f} tokens (measured from this data)."
            if ratio
            else "0.25 tokens (assumed: no first request to measure)."
        ),
    ]
    for f in cache:
        lines += [
            "",
            f"Observation: {f.text}; {f.cost:,} input-equivalent tokens a week.",
        ]
    if fixed:
        lines += [
            "",
            f"{_n(fixed, 'item is', 'items are')} absent from the sessions of the last"
            f" {rules.current_days} days: they look already fixed, not listed.",
        ]
    if errors or hooks or truncated:
        top_errors = sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
        err_text = ", ".join(f"{t} {n}" for t, n in top_errors) or "-"
        lines += [
            "",
            f"Tool errors: {err_text} · hook blocks: {hooks}"
            f" · truncated reads: {truncated}",
        ]
    carried = sorted(
        ((n, c) for (k, n), c in wpart.items() if k in ("file", "rule", "skill_body")),
        key=lambda kv: (-kv[1], kv[0]),
    )[:6]
    lines += [
        "",
        "## 3. Not visible",
        "",
        _coverage(conn, start, end),
        *([health] if health else []),
        "- The session title, the compaction summary and suggestion requests are not"
        " written to the transcript; this report cannot see them.",
        "- What the tokens bought (was the job done, was the result right) is not in"
        " the records. The report sees cost and what is unused, not value.",
        "",
        "## 4. Optional: Claude reading (model work)",
        "",
        "The most carried files and skill bodies. If you want, Claude reads them with"
        " these questions: does it change behavior, does it reach the right reader,"
        " is it in the right place, does it repeat Claude's own default?",
        "",
    ]
    lines += [
        f"- {name} — share of cost {_share(tok(c * week), paid_week)}"
        for name, c in carried
    ]
    return "\n".join(lines) + "\n"
