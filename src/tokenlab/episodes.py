"""Episode table (placement/codex-delegation-design.md, first slice).

An episode opens at a prompt turn in a main thread (human, sdk or scheduled; prompts
that arrive before any request answers them are one bundle) and owns the main
requests until the next prompt, plus the full subtree of agents spawned from them.
Each row carries the main context at its start, how much it grew, its cost in main
and in its agents (exclusive and inclusive of nested agents), main cost before,
during and after its agents ran, cache rewrites (compaction ones apart), and weak
attention proxies. The report opens with an audit of what could not be attributed.
No "delegation paid off" verdict: that needs matched cohorts first.
Prompts and tool targets are never printed. Same ledger, same bytes.
"""

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from tokenlab.prices import PER_TOKEN, effective_rates, rates_for

BANDS = ((100_000, "<100k"), (200_000, "100-200k"), (300_000, "200-300k"))
TOP_BAND = "300k+"
BAND_ORDER = tuple(name for _, name in BANDS) + (TOP_BAND,)
PROMPT_KINDS = ("human", "sdk", "scheduled")  # turns that open an episode
MISS_SLACK = 5000  # as in report.py: a read this far below the last prompt is a miss
OWN_TOOLS_DELEGATED = 2  # main tool calls besides Agent allowed in a "delegated" turn
META_TOOLS = frozenset({"ToolSearch", "TodoWrite", "TaskCreate", "TaskUpdate"})
KINDS = ("inline", "delegated", "mixed")
FILE_TOOLS = frozenset({"Read", "Edit", "MultiEdit", "Write"})
EDIT_TOOLS = frozenset({"Edit", "MultiEdit", "Write"})
COMPACTION_K = 20  # main requests compared on each side of a compaction event
SHELL_TOOLS = frozenset({"Bash", "PowerShell"})


def band(prompt: int) -> str:
    return next((name for top, name in BANDS if prompt < top), TOP_BAND)


@dataclass
class Req:
    seq: int
    ts_first: str
    ts_last: str
    model: str
    speed: str | None
    prompt: int  # input + cache read + cache write
    read: int
    w5: int
    w1: int
    answer: int  # output minus thinking: what stays in context
    cost: float | None


@dataclass
class Episode:
    thread: str
    source: str  # kind of the opening turn: human | sdk | scheduled
    start_ts: str
    reqs: list[Req]
    prompts: int = 1  # prompt turns bundled into this episode
    prompt_source: str = "unknown"  # typed | queued | sdk | scheduled | unknown
    next_is_correction: bool = False
    roots: list[str] = field(default_factory=list)  # agent threads main spawned
    max_parallel: int = 0  # most agents spawned by one main request
    own_tools: int = 0  # main tool calls besides Agent and meta tools
    results: int = 0
    errors: int = 0
    hooks: int = 0
    rejects: int = 0
    approvals: int = 0
    reads: int = 0
    rereads: int = 0  # Read of a file already read since the last compaction
    edits: int = 0  # Edit, MultiEdit and Write calls in main (mutations)
    re_edits: int = 0  # ... on a file this episode already edited
    edit_calls: int = 0  # Edit calls only: the ones whose reversal can be seen
    reverts: int = 0  # Edit whose old>new is an earlier Edit's new>old, same file
    shell_runs: int = 0
    retries: int = 0  # shell command run again after it failed in this episode
    failed_again: int = 0  # ... and failed again
    after_mutations: int = 0  # main Edit/MultiEdit/Write calls after the last agent
    mutation_rework: int = 0  # ... on a file one of this episode's agents touched
    after_reads: int = 0  # main Read calls after the last agent finished
    read_overlap: int = 0  # ... on a file one of this episode's agents touched
    next_chars: int = -1  # length of the next prompt, -1 when none
    next_gap_s: float = -1.0  # last request end to the next prompt, -1 when none
    interrupts: int = 0
    misses: int = 0  # cache rewrites not explained by a compaction
    miss_cost: float = 0.0
    compaction_rewrites: int = 0
    sub_exclusive: float = 0.0  # agents main spawned directly
    sub_inclusive: float = 0.0  # ... plus every nested descendant
    nested: int = 0  # descendants below the roots
    main_before: float = 0.0  # main cost before the first dispatch
    main_during: float = 0.0  # ... while an agent of this episode was running
    main_gap: float = 0.0  # ... between agent runs, none of them running
    main_after: float = 0.0  # ... after the last agent finished
    wait_s: float = 0.0  # first dispatch to last agent finish
    active_s: float = 0.0  # time at least one agent of this episode was running
    # None when a compaction fell inside the episode: the last and first prompt then
    # sit in different contexts and growth, and the carry built on it, mean nothing.
    growth: int | None = 0  # tokens this episode left in main context
    carry_requests: int | None = 0  # later main requests until compaction or end
    carry_cost: float | None = 0.0  # growth re-read by those requests, cache-read price
    compacted: bool = False

    @property
    def start(self) -> int:
        return self.reqs[0].prompt

    @property
    def kind(self) -> str:
        if not self.roots:
            return "inline"
        return "delegated" if self.own_tools <= OWN_TOOLS_DELEGATED else "mixed"

    @property
    def main_cost(self) -> float:
        return sum(r.cost or 0.0 for r in self.reqs)

    @property
    def total(self) -> float:
        return self.main_cost + self.sub_inclusive


def prompt_source(kind: str, source: str) -> str:
    """typed | queued | sdk | scheduled | unknown, from the opening turn's kind and
    its turns.source (turnOrigin/promptSource/entrypoint). Anything else, such as a
    human turn with no promptSource or an accepted suggestion, is unknown."""
    if kind in ("sdk", "scheduled"):
        return kind
    given = source.split("/")[1] if source.count("/") == 2 else "-"
    return given if given in ("typed", "queued") else "unknown"


def _secs(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class Tree:
    """Who spawned whom. A subagent names its spawning call (tool_use_id); the
    caller is the main thread for depth 1, otherwise the thread meta parentAgentId
    names, else the one subagent one level up that made that call."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        threads = {
            tid: (sess, kind, depth)
            for tid, sess, kind, depth in conn.execute(
                "SELECT thread_id, session_id, kind, spawn_depth FROM threads"
            )
        }
        callers: dict[tuple[str, str], list[str]] = defaultdict(list)
        spawn: dict[tuple[str, str], tuple[int, str, int | None, int | None]] = {}
        for tid, tu, seq, ts, brief, bg in conn.execute(
            "SELECT thread_id, tool_use_id, request_seq, ts, brief, background"
            " FROM content WHERE kind = 'tool_use' AND tool = 'Agent'"
            " ORDER BY thread_id"
        ):
            callers[(threads[tid][0], tu)].append(tid)
            spawn[(tid, tu)] = (seq, ts or "", brief, bg)
        self.parent: dict[str, str] = {}
        self.spawned_at: dict[str, int] = {}  # child -> parent's request seq
        self.dispatched: dict[str, str] = {}  # child -> when the Agent call was made
        self.brief: dict[str, int | None] = {}  # child -> prompt characters
        self.background: dict[str, int | None] = {}  # child -> run_in_background
        self.child: dict[tuple[str, str], str] = {}  # (parent, tool_use_id) -> child
        self.children: dict[str, list[str]] = defaultdict(list)
        self.unlinked: list[str] = []
        self.ambiguous = 0  # tool_use_id shared by several callers
        for tid, sess, tu, depth, named in conn.execute(
            "SELECT thread_id, session_id, tool_use_id, spawn_depth, parent_agent_id"
            " FROM threads WHERE kind = 'subagent' ORDER BY thread_id"
        ):
            found = [c for c in callers.get((sess, tu), []) if c != tid]
            self.ambiguous += len(found) > 1
            if named:
                found = [c for c in found if c.split("@")[0] == f"agent-{named}"]
            elif depth == 1 or depth is None:
                found = [c for c in found if threads[c][1] == "main"] or found
            else:
                found = [c for c in found if threads[c][2] == depth - 1] or found
            if len(found) != 1:
                self.unlinked.append(tid)
                continue
            self.parent[tid] = found[0]
            seq, ts, brief, bg = spawn[(found[0], tu)]
            self.spawned_at[tid], self.dispatched[tid] = seq, ts
            self.brief[tid], self.background[tid] = brief, bg
            self.child[(found[0], tu)] = tid
            self.children[found[0]].append(tid)
        self.cost: dict[str, float] = {}
        self.first_ts: dict[str, str] = {}
        self.last_ts: dict[str, str] = {}
        for tid, cost, first, last in conn.execute(
            "SELECT thread_id, SUM(COALESCE(cost_usd, 0)), MIN(ts_first),"
            " MAX(ts_last) FROM requests GROUP BY 1"
        ):
            self.cost[tid] = cost
            self.first_ts[tid] = first
            self.last_ts[tid] = last
        self._subtree: dict[str, tuple[float, int, str]] = {}
        self.touched: dict[str, set[str]] = defaultdict(set)  # files an agent used
        for tid, target in conn.execute(
            "SELECT c.thread_id, c.target FROM content c JOIN threads t"
            " USING (thread_id) WHERE t.kind = 'subagent' AND c.kind = 'tool_use'"
            " AND c.tool IN ('Read', 'Edit', 'MultiEdit', 'Write')"
            " AND c.target IS NOT NULL"
        ):
            self.touched[tid].add(target)

    def files(self, tid: str) -> set[str]:
        """Files a thread and its descendants read or wrote."""
        out = set(self.touched.get(tid, set()))
        for c in self.children.get(tid, []):
            out |= self.files(c)
        return out

    def subtree(self, tid: str) -> tuple[float, int, str]:
        """Inclusive cost, number of descendants, and when the last of them ended."""
        if tid in self._subtree:
            return self._subtree[tid]
        self._subtree[tid] = (0.0, 0, "")  # cycle guard
        cost, n, last = self.cost.get(tid, 0.0), 0, self.last_ts.get(tid, "")
        for c in self.children.get(tid, []):
            sub_cost, sub_n, sub_last = self.subtree(c)
            cost, n, last = cost + sub_cost, n + 1 + sub_n, max(last, sub_last)
        self._subtree[tid] = (cost, n, last)
        return self._subtree[tid]


def _window_start(conn: sqlite3.Connection, days: int) -> str | None:
    end = conn.execute("SELECT MAX(ts_last) FROM requests").fetchone()[0]
    if end is None:
        return None
    start = datetime.fromisoformat(end) - timedelta(days=days)
    return start.isoformat().replace("+00:00", "Z")


def _requests(conn: sqlite3.Connection, thread: str) -> list[Req]:
    return [
        Req(seq, f, last, m, sp, inp + rd + wr, rd, w5, w1, max(0, out - th), cost)
        for seq, f, last, m, sp, inp, rd, wr, w5, w1, out, th, cost in conn.execute(
            "SELECT seq, ts_first, ts_last, model, speed, input, cache_read,"
            " cache_write, cache_write_5m, cache_write_1h, output, thinking, cost_usd"
            " FROM requests WHERE thread_id = ? ORDER BY seq",
            (thread,),
        )
    ]


@dataclass
class Audit:
    """What the window holds and what could not be put in an episode."""

    prompt_turns: Counter[str] = field(default_factory=Counter)
    bundled: int = 0  # prompt turns merged into the episode of an earlier one
    unanswered: int = 0  # prompt turns no request followed
    bundled_in_window: int = 0  # prompt turns inside the kept episodes
    early_prompts: int = 0  # window prompt turns bundled into a pre-window episode
    unowned_requests: int = 0  # main requests before any prompt turn
    unowned_cost: float = 0.0
    early_requests: int = 0  # window requests of an episode that began before it
    early_cost: float = 0.0
    interrupts: int = 0
    # interrupts outside every episode, by what the request before them was
    interrupt_reasons: Counter[str] = field(default_factory=Counter)
    episode_requests: int = 0  # main requests inside kept episodes


def episodes(
    conn: sqlite3.Connection,
    days: int,
    tree: Tree | None = None,
    audit: Audit | None = None,
) -> list[Episode]:
    start = _window_start(conn, days)
    if start is None:
        return []
    tree = tree or Tree(conn)
    audit = audit if audit is not None else Audit()
    out: list[Episode] = []
    for (main,) in conn.execute(
        "SELECT thread_id FROM threads WHERE kind = 'main' ORDER BY thread_id"
    ).fetchall():
        out += _thread_episodes(conn, main, start, tree, audit)
    return out


def _thread_episodes(
    conn: sqlite3.Connection, main: str, start: str, tree: Tree, audit: Audit
) -> list[Episode]:
    reqs = _requests(conn, main)
    by_seq = {r.seq: r for r in reqs}
    turns = conn.execute(
        "SELECT request_seq, ts, kind, correction, chars, source FROM turns"
        " WHERE thread_id = ? ORDER BY request_seq, ts",
        (main,),
    ).fetchall()
    # Prompt turns after the same request form one bundle: no request answered the
    # earlier ones on their own.
    # seq, ts, kind, prompts, prompts in the window, corrects, source of the first
    bundles: list[tuple[int, str, str, int, int, bool, str]] = []
    first_chars: dict[int, int] = {}  # bundle seq -> chars of its first prompt
    for seq, ts, kind, corr, chars, source in turns:
        if kind not in PROMPT_KINDS:
            continue
        inside = int(ts >= start)
        audit.prompt_turns[kind] += inside
        if bundles and bundles[-1][0] == seq:
            s, t0, k0, n, w, c0, src0 = bundles[-1]
            bundles[-1] = (s, t0, k0, n + 1, w + inside, c0 or bool(corr), src0)
            audit.bundled += inside
        else:
            bundles.append((seq, ts, kind, 1, inside, bool(corr), source))
            first_chars[seq] = chars
    bounds = [b[0] for b in bundles] + [reqs[-1].seq if reqs else -1]
    mine: list[Episode] = []
    early: set[int] = set()  # requests of episodes that began before the window
    for i, (_, ts0, kind0, n, inside, _, src0) in enumerate(bundles):
        span = [r for r in reqs if bounds[i] < r.seq <= bounds[i + 1]]
        if ts0 < start:
            early.update(r.seq for r in span)
            audit.early_prompts += inside
            continue
        if not span:
            audit.unanswered += n
            continue
        e = Episode(
            main, kind0, ts0, span, prompts=n, prompt_source=prompt_source(kind0, src0)
        )
        if i + 1 < len(bundles):
            nseq, nts = bundles[i + 1][0], bundles[i + 1][1]
            e.next_is_correction = bundles[i + 1][5]
            e.next_chars = first_chars[nseq]
            e.next_gap_s = max(0.0, _secs(nts) - _secs(span[-1].ts_last))
        audit.bundled_in_window += n
        mine.append(e)
    owner = {r.seq: e for e in mine for r in e.reqs}
    audit.episode_requests += len(owner)
    for r in reqs:
        if r.ts_first < start or r.seq in owner:
            continue
        if r.seq in early:
            audit.early_requests += 1
            audit.early_cost += r.cost or 0.0
        else:
            audit.unowned_requests += 1
            audit.unowned_cost += r.cost or 0.0
    for seq, ts, kind, _, _, _ in turns:
        if kind == "interrupt" and ts >= start:
            audit.interrupts += 1
            if seq in owner:
                owner[seq].interrupts += 1
                continue
            prior = by_seq.get(seq)
            if prior is None:
                reason = "no request before it"
            elif prior.ts_first < start:
                reason = "request before the window"
            elif seq in early:
                reason = "request of an episode begun before the window"
            else:
                reason = "request before any prompt"
            audit.interrupt_reasons[reason] += 1
    if not mine:
        return []

    compactions = [
        ts
        for (ts,) in conn.execute(
            "SELECT ts FROM events WHERE thread_id = ? AND subtype = 'compact_boundary'"
            " ORDER BY ts",
            (main,),
        )
    ]
    pending = list(compactions)
    seen: set[str] = set()
    spawns: Counter[int] = Counter()
    content = conn.execute(
        "SELECT request_seq, kind, tool_use_id, tool, target, is_error, decision,"
        " fingerprint, ts FROM content WHERE thread_id = ?"
        " ORDER BY request_seq, kind DESC",
        (main,),
    ).fetchall()
    failed = {
        tu for _, kind, tu, _, _, err, _, _, _ in content if kind != "tool_use" and err
    }
    # Per episode: files edited (with the old>new of each edit), failed commands,
    # and main file calls with their time and tool, for rework after agents.
    edited: dict[int, dict[str, list[str]]] = defaultdict(dict)
    bad_cmds: dict[int, set[str]] = defaultdict(set)
    file_calls: dict[int, list[tuple[str, str, str]]] = defaultdict(list)
    for seq, kind, tu, tool, target, err, dec, fp, call_ts in content:
        req = by_seq.get(seq)
        while req is not None and pending and pending[0] <= req.ts_first:
            pending.pop(0)
            seen.clear()
        ep = owner.get(seq)
        if kind == "tool_use":
            if tool == "Read" and target:
                if ep is not None:
                    ep.reads += 1
                    ep.rereads += target in seen
                seen.add(target)
            if ep is None:
                continue
            key = id(ep)
            if tool in FILE_TOOLS and target and req is not None:
                file_calls[key].append((call_ts or req.ts_first, target, tool))
            if tool in EDIT_TOOLS and target:
                ep.edits += 1
                history = edited[key].setdefault(target, [])
                ep.re_edits += bool(history)
                # Only Edit can be reversed exactly: Write keeps a one-way hash and
                # MultiEdit hashes its whole list.
                if tool == "Edit":
                    ep.edit_calls += 1
                    if fp and ">" in fp:
                        old, new = fp.split(">", 1)
                        ep.reverts += f"{new}>{old}" in history
                history.append(fp or "")
            if tool in SHELL_TOOLS and fp:
                ep.shell_runs += 1
                if fp in bad_cmds[key]:
                    ep.retries += 1
                    ep.failed_again += tu in failed
                if tu in failed:
                    bad_cmds[key].add(fp)
            child = tree.child.get((main, tu)) if tool == "Agent" else None
            if child is not None:
                ep.roots.append(child)
                spawns[seq] += 1
            elif tool != "Agent" and tool not in META_TOOLS:
                ep.own_tools += 1
        elif ep is not None:
            ep.results += 1
            ep.errors += bool(err)
            if dec:
                ep.hooks += dec.startswith("hook")
                ep.rejects += dec.startswith("reject")
                ep.approvals += dec == "approved:user"

    for e in mine:
        finished = _agents(e, tree, spawns)
        if finished:
            files = set().union(*(tree.files(r) for r in e.roots))
            later = [(t, tool) for ts, t, tool in file_calls[id(e)] if ts > finished]
            mutations = [t for t, tool in later if tool in EDIT_TOOLS]
            reads = [t for t, tool in later if tool == "Read"]
            e.after_mutations = len(mutations)
            e.mutation_rework = sum(t in files for t in mutations)
            e.after_reads = len(reads)
            e.read_overlap = sum(t in files for t in reads)
        _cache(e, by_seq, compactions)
        first, last = e.reqs[0], e.reqs[-1]
        e.compacted = any(first.ts_first <= c <= last.ts_last for c in compactions)
        if e.compacted:
            e.growth = e.carry_requests = e.carry_cost = None
            continue
        growth = max(0, last.prompt + last.answer - first.prompt)
        e.growth = growth
        stop = next((c for c in compactions if c > last.ts_last), "~")
        later_reqs = [r for r in reqs if r.seq > last.seq and r.ts_first < stop]
        e.carry_requests, e.carry_cost = len(later_reqs), 0.0
        for r in later_reqs:
            rates = effective_rates(r.model, r.prompt, r.speed == "fast")
            if rates is not None:
                e.carry_cost += growth * rates.cache_read * PER_TOKEN
    return mine


def _agents(e: Episode, tree: Tree, spawns: Counter[int]) -> str:
    """Splits main cost around the agents' run; returns when the last finished."""
    e.max_parallel = max((spawns[r.seq] for r in e.reqs), default=0)
    if not e.roots:
        e.main_before = e.main_cost
        return ""
    runs: list[tuple[str, str]] = []  # each root: dispatched, subtree finished
    for root in e.roots:
        e.sub_exclusive += tree.cost.get(root, 0.0)
        cost, n, last = tree.subtree(root)
        e.sub_inclusive += cost
        e.nested += n
        begun = tree.dispatched.get(root) or tree.first_ts.get(root, "")
        runs.append((begun, max(last, begun)))
    merged: list[list[str]] = []
    for a, b in sorted(runs):
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    dispatch, finished = merged[0][0], merged[-1][1]
    for r in e.reqs:
        cost = r.cost or 0.0
        if r.ts_first <= dispatch:
            e.main_before += cost
        elif r.ts_first > finished:
            e.main_after += cost
        elif any(a <= r.ts_first <= b for a, b in merged):
            e.main_during += cost
        else:
            e.main_gap += cost
    e.wait_s = max(0.0, _secs(finished) - _secs(dispatch))
    e.active_s = sum(max(0.0, _secs(b) - _secs(a)) for a, b in merged)
    return finished


@dataclass
class Window:
    """What happened in some main requests of one thread: the K before or the K
    after a compaction. Reads, edits and shell runs are matched within the window
    only, so both sides start from nothing."""

    prompts: list[int] = field(default_factory=list)  # main prompt of each request
    results: int = 0
    errors: int = 0
    hooks: int = 0
    rejects: int = 0
    reads: int = 0
    rereads: int = 0
    mutations: int = 0
    re_edits: int = 0
    edit_calls: int = 0
    reverts: int = 0
    shell_runs: int = 0
    failed_again: int = 0
    interrupts: int = 0
    human: int = 0
    corrections: int = 0

    def add(self, other: "Window") -> None:
        for name in self.__dataclass_fields__:
            if name == "prompts":
                self.prompts += other.prompts
            else:
                setattr(self, name, getattr(self, name) + getattr(other, name))

    def cells(self) -> list[str]:
        n = len(self.prompts)
        per_100 = f"{100 * self.interrupts / n:.1f}" if n else "-"
        return [
            f"{_med([float(p) for p in self.prompts]):,.0f}" if n else "-",
            _pct(self.errors, self.results),
            _pct(self.hooks, self.results),
            _pct(self.rejects, self.results),
            _pct(self.rereads, self.reads),
            _pct(self.re_edits, self.mutations),
            _pct(self.reverts, self.edit_calls),
            _pct(self.failed_again, self.shell_runs),
            per_100,
            _pct(self.corrections, self.human),
        ]


WINDOW_HEAD = (
    "median prompt | tool errors | hook blocks | rejected | rereads"
    " | re-edits | exact Edit reversal | failed cmd failed again"
    " | interrupts per 100 requests | human turns that correct"
)


@dataclass
class Compaction:
    """One compact_boundary event with COMPACTION_K main requests either side."""

    thread: str
    ts: str
    before: Window
    after: Window
    span: tuple[int, int]  # request seqs the two windows would cover, edge to edge
    overlaps: bool = False  # another event of this thread shares a request with it

    @property
    def partial(self) -> bool:
        return min(len(self.before.prompts), len(self.after.prompts)) < COMPACTION_K


def _window(
    reqs: list[Req],
    content: list[
        tuple[int, str, str, str | None, str | None, int, str | None, str | None]
    ],
    turns: list[tuple[int, str, int]],
    failed: set[str],
) -> Window:
    w = Window(prompts=[r.prompt for r in reqs])
    seqs = {r.seq for r in reqs}
    seen: set[str] = set()
    edited: dict[str, list[str]] = {}
    bad: set[str] = set()
    for seq, kind, tu, tool, target, err, dec, fp in content:
        if seq not in seqs:
            continue
        if kind != "tool_use":
            w.results += 1
            w.errors += bool(err)
            if dec:
                w.hooks += dec.startswith("hook")
                w.rejects += dec.startswith("reject")
            continue
        if tool == "Read" and target:
            w.reads += 1
            w.rereads += target in seen
            seen.add(target)
        if tool in EDIT_TOOLS and target:
            w.mutations += 1
            history = edited.setdefault(target, [])
            w.re_edits += bool(history)
            if tool == "Edit":
                w.edit_calls += 1
                if fp and ">" in fp:
                    old, new = fp.split(">", 1)
                    w.reverts += f"{new}>{old}" in history
            history.append(fp or "")
        if tool in SHELL_TOOLS and fp:
            w.shell_runs += 1
            if fp in bad:
                w.failed_again += tu in failed
            if tu in failed:
                bad.add(fp)
    for seq, kind, corr in turns:
        if seq in seqs:
            w.interrupts += kind == "interrupt"
            w.human += kind == "human"
            w.corrections += bool(corr)
    return w


def compactions(conn: sqlite3.Connection, days: int) -> list[Compaction]:
    """One entry per compact_boundary event in the window, none merged: the
    COMPACTION_K main requests of the thread before it against the K after it."""
    start = _window_start(conn, days)
    if start is None:
        return []
    out: list[Compaction] = []
    for main, ts in conn.execute(
        "SELECT e.thread_id, e.ts FROM events e JOIN threads t USING (thread_id)"
        " WHERE t.kind = 'main' AND e.subtype = 'compact_boundary' AND e.ts >= ?"
        " ORDER BY e.thread_id, e.ts",
        (start,),
    ).fetchall():
        if not out or out[-1].thread != main:
            reqs = _requests(conn, main)
            content = conn.execute(
                "SELECT request_seq, kind, tool_use_id, tool, target, is_error,"
                " decision, fingerprint FROM content WHERE thread_id = ?"
                " ORDER BY request_seq, kind DESC",
                (main,),
            ).fetchall()
            failed = {
                tu
                for _, kind, tu, _, _, err, _, _ in content
                if kind != "tool_use" and err
            }
            turns = conn.execute(
                "SELECT request_seq, kind, correction FROM turns WHERE thread_id = ?",
                (main,),
            ).fetchall()
        cut = sum(r.ts_first < ts for r in reqs)  # requests before the event
        before, after = (
            reqs[max(0, cut - COMPACTION_K) : cut],
            reqs[cut : cut + COMPACTION_K],
        )
        out.append(
            Compaction(
                main,
                ts,
                _window(before, content, turns, failed),
                _window(after, content, turns, failed),
                (cut - COMPACTION_K, cut + COMPACTION_K - 1),
            )
        )
    for a, b in zip(out, out[1:], strict=False):
        if a.thread == b.thread and b.span[0] <= a.span[1]:
            a.overlaps = b.overlaps = True
    return out


def _cache(e: Episode, by_seq: dict[int, Req], compactions: list[str]) -> None:
    for r in e.reqs:
        before = by_seq.get(r.seq - 1)
        if before is None or r.read >= before.prompt - MISS_SLACK:
            continue
        if any(before.ts_last <= c <= r.ts_first for c in compactions):
            e.compaction_rewrites += 1
            continue
        e.misses += 1
        rates = effective_rates(r.model, r.prompt, r.speed == "fast")
        if rates is not None:
            e.miss_cost += (
                r.w5 * rates.cache_write_5m + r.w1 * rates.cache_write_1h
            ) * PER_TOKEN


def _med(xs: list[float]) -> float:
    xs = sorted(xs)
    if not xs:
        return 0.0
    mid = len(xs) // 2
    return xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2


def _med_known(xs: list[float | None]) -> float | None:
    known = [x for x in xs if x is not None]
    return _med(known) if known else None


def _fmt(x: float | None, spec: str) -> str:
    return "-" if x is None else format(x, spec)


def _null(x: int | None) -> object:
    return "" if x is None else x


def _pct(part: float, whole: float) -> str:
    return f"{part / whole:.1%}" if whole else "-"


SIGNAL_HEAD = (
    " | tool errors | hook blocks | rejected | rereads | re-edits"
    " | exact Edit reversal | failed cmd failed again"
    " | mutation rework after agents | read overlap after agents | interrupted"
    " | next turn corrects |"
)
SIGNAL_RULE = "|---" * 11


def _signals(es: list[Episode]) -> str:
    results = sum(e.results for e in es)
    edits = sum(e.edits for e in es)
    after_mutations = sum(e.after_mutations for e in es)
    return (
        f" | {_pct(sum(e.errors for e in es), results)}"
        f" | {_pct(sum(e.hooks for e in es), results)}"
        f" | {_pct(sum(e.rejects for e in es), results)}"
        f" | {_pct(sum(e.rereads for e in es), sum(e.reads for e in es))}"
        f" | {_pct(sum(e.re_edits for e in es), edits)}"
        f" | {_pct(sum(e.reverts for e in es), sum(e.edit_calls for e in es))}"
        f" | {_pct(sum(e.failed_again for e in es), sum(e.shell_runs for e in es))}"
        f" | {_pct(sum(e.mutation_rework for e in es), after_mutations)}"
        f" | {_pct(sum(e.read_overlap for e in es), sum(e.after_reads for e in es))}"
        f" | {_pct(sum(e.interrupts > 0 for e in es), len(es))}"
        f" | {_pct(sum(e.next_is_correction for e in es), len(es))} |"
    )


ROW_FIELDS = (
    "thread",
    "start_ts",
    "source",
    "prompt_source",
    "kind",
    "band",
    "prompts",
    "requests",
    "start_prompt",
    "growth",
    "roots",
    "max_parallel",
    "nested",
    "own_tools",
    "main_usd",
    "main_before_usd",
    "main_during_usd",
    "main_gap_usd",
    "main_after_usd",
    "agents_exclusive_usd",
    "agents_inclusive_usd",
    "total_usd",
    "wait_s",
    "active_s",
    "carry_requests",
    "carry_usd",
    "misses",
    "miss_usd",
    "compaction_rewrites",
    "compacted",
    "results",
    "errors",
    "hooks",
    "rejects",
    "approvals",
    "reads",
    "rereads",
    "interrupts",
    "next_is_correction",
    "edits",
    "re_edits",
    "edit_calls",
    "reverts",
    "shell_runs",
    "retries",
    "failed_again",
    "after_mutations",
    "mutation_rework",
    "after_reads",
    "read_overlap",
    "next_chars",
    "next_gap_s",
)


def rows(conn: sqlite3.Connection, days: int = 30) -> str:
    """One tab-separated row per episode, for auditing outliers by hand."""
    lines = ["\t".join(ROW_FIELDS)]
    for e in sorted(episodes(conn, days), key=lambda e: (e.start_ts, e.thread)):
        values: tuple[object, ...] = (
            e.thread,
            e.start_ts,
            e.source,
            e.prompt_source,
            e.kind,
            band(e.start),
            e.prompts,
            len(e.reqs),
            e.start,
            _null(e.growth),
            len(e.roots),
            e.max_parallel,
            e.nested,
            e.own_tools,
            f"{e.main_cost:.4f}",
            f"{e.main_before:.4f}",
            f"{e.main_during:.4f}",
            f"{e.main_gap:.4f}",
            f"{e.main_after:.4f}",
            f"{e.sub_exclusive:.4f}",
            f"{e.sub_inclusive:.4f}",
            f"{e.total:.4f}",
            f"{e.wait_s:.0f}",
            f"{e.active_s:.0f}",
            _null(e.carry_requests),
            "" if e.carry_cost is None else f"{e.carry_cost:.4f}",
            e.misses,
            f"{e.miss_cost:.4f}",
            e.compaction_rewrites,
            int(e.compacted),
            e.results,
            e.errors,
            e.hooks,
            e.rejects,
            e.approvals,
            e.reads,
            e.rereads,
            e.interrupts,
            int(e.next_is_correction),
            e.edits,
            e.re_edits,
            e.edit_calls,
            e.reverts,
            e.shell_runs,
            e.retries,
            e.failed_again,
            e.after_mutations,
            e.mutation_rework,
            e.after_reads,
            e.read_overlap,
            e.next_chars,
            f"{e.next_gap_s:.0f}",
        )
        lines.append("\t".join(map(str, values)))
    return "\n".join(lines) + "\n"


EDGE_FIELDS = (
    "episode_thread",
    "episode_start",
    "parent",
    "child",
    "generation",
    "dispatched",
    "first_request",
    "last_request",
    "subtree_finished",
    "brief_chars",
    "background",
    "exclusive_usd",
    "inclusive_usd",
)


def edges(conn: sqlite3.Connection, days: int = 30) -> str:
    """One tab-separated row per agent reached from an episode: who spawned it,
    when, how long it and its descendants ran, and what they cost."""
    tree = Tree(conn)
    lines = ["\t".join(EDGE_FIELDS)]
    for e in sorted(episodes(conn, days, tree), key=lambda e: (e.start_ts, e.thread)):
        stack = [(r, 1) for r in reversed(e.roots)]
        while stack:
            child, generation = stack.pop()
            cost, _, finished = tree.subtree(child)
            values: tuple[object, ...] = (
                e.thread,
                e.start_ts,
                tree.parent.get(child, ""),
                child,
                generation,
                tree.dispatched.get(child, ""),
                tree.first_ts.get(child, ""),
                tree.last_ts.get(child, ""),
                finished,
                tree.brief.get(child) if tree.brief.get(child) is not None else "",
                tree.background.get(child)
                if tree.background.get(child) is not None
                else "",
                f"{tree.cost.get(child, 0.0):.4f}",
                f"{cost:.4f}",
            )
            lines.append("\t".join(map(str, values)))
            stack += [(c, generation + 1) for c in reversed(tree.children[child])]
    return "\n".join(lines) + "\n"


def _audit_lines(
    conn: sqlite3.Connection,
    start: str,
    es: list[Episode],
    tree: Tree,
    audit: Audit,
    events: list[Compaction],
) -> list[str]:
    """Checks whose expected side is counted straight from the ledger, never from
    the episode walk being checked."""
    raw_prompts = dict(
        conn.execute(
            "SELECT u.kind, COUNT(*) FROM turns u JOIN threads t USING (thread_id)"
            " WHERE t.kind = 'main' AND u.ts >= ?"
            " AND u.kind IN ('human', 'sdk', 'scheduled') GROUP BY 1",
            (start,),
        ).fetchall()
    )
    (raw_requests,) = conn.execute(
        "SELECT COUNT(*) FROM requests r JOIN threads t USING (thread_id)"
        " WHERE t.kind = 'main' AND r.ts_first >= ?",
        (start,),
    ).fetchone()
    (raw_interrupts,) = conn.execute(
        "SELECT COUNT(*) FROM turns u JOIN threads t USING (thread_id)"
        " WHERE t.kind = 'main' AND u.kind = 'interrupt' AND u.ts >= ?",
        (start,),
    ).fetchone()
    (raw_compactions,) = conn.execute(
        "SELECT COUNT(*) FROM events e JOIN threads t USING (thread_id)"
        " WHERE t.kind = 'main' AND e.subtype = 'compact_boundary' AND e.ts >= ?",
        (start,),
    ).fetchone()
    # Episodes with a compaction event between their first and last request, found
    # in the ledger, against the episodes that report no growth.
    spanning = {
        id(e)
        for e in es
        if conn.execute(
            "SELECT 1 FROM events WHERE thread_id = ? AND subtype = 'compact_boundary'"
            " AND ts >= ? AND ts <= ?",
            (e.thread, e.reqs[0].ts_first, e.reqs[-1].ts_last),
        ).fetchone()
    }
    nulled = {id(e) for e in es if e.growth is None}
    unpriced_models = sorted(
        m
        for (m,) in conn.execute(
            "SELECT DISTINCT model FROM requests WHERE cost_usd IS NULL"
            " AND ts_first >= ?",
            (start,),
        )
    )
    (window_cost, unpriced, unpriced_tokens) = conn.execute(
        "SELECT SUM(COALESCE(cost_usd, 0)), COALESCE(SUM(cost_usd IS NULL), 0),"
        " COALESCE(SUM(CASE WHEN cost_usd IS NULL THEN input + cache_read"
        " + cache_write + output ELSE 0 END), 0) FROM requests WHERE ts_first >= ?",
        (start,),
    ).fetchone()
    # Agents with any request in the window, and what of their cost falls in it.
    in_window: dict[str, float] = dict(
        conn.execute(
            "SELECT t.thread_id, SUM(CASE WHEN r.ts_first >= ? THEN"
            " COALESCE(r.cost_usd, 0) ELSE 0 END) FROM threads t"
            " JOIN requests r USING (thread_id) WHERE t.kind = 'subagent'"
            " GROUP BY 1 HAVING MAX(r.ts_first) >= ?",
            (start, start),
        ).fetchall()
    )
    reached = {r for e in es for r in e.roots}
    stack = list(reached)
    while stack:
        for c in tree.children.get(stack.pop(), []):
            if c not in reached:
                reached.add(c)
                stack.append(c)
    unlinked = [t for t in tree.unlinked if t in in_window]
    outside = sorted(set(in_window) - reached - set(unlinked))
    # An agent outside every episode is explained when the main request that
    # spawned its root is outside every episode too.
    owned = {(e.thread, r.seq) for e in es for r in e.reqs}

    def root_spawn(t: str) -> tuple[str, int]:
        while tree.parent.get(t) in tree.parent:
            t = tree.parent[t]
        return tree.parent.get(t, ""), tree.spawned_at.get(t, -1)

    # Outside and explained: its root hangs off a main request that is itself
    # outside every episode. A root with no known spawn is unexplained.
    unexplained_agents = [
        t for t in outside if root_spawn(t)[0] == "" or root_spawn(t) in owned
    ]
    # Every agent in the window must be either linked, with a spawn time, or
    # listed as unlinked; anything else means the walk lost it.
    lost = [t for t in in_window if t not in tree.parent and t not in tree.unlinked]
    no_dispatch = [t for t in in_window if t in tree.parent and not tree.dispatched[t]]
    outside_cost = sum(in_window[t] for t in outside + unlinked)
    # Episode agent cost counts whole agent threads; an agent that began before
    # the window adds its pre-window requests there, so take them back out.
    pre_window = sum(tree.cost.get(t, 0.0) - in_window.get(t, 0.0) for t in reached)
    in_episodes = sum(e.total for e in es) - pre_window
    residual = window_cost - (
        in_episodes + audit.unowned_cost + audit.early_cost + outside_cost
    )

    def status(holds: bool, partition: bool, explained: bool = False) -> str:
        """yes: holds. excluded: the parts add up to the raw count and every
        excluded item has a stated reason. NO: anything else."""
        if not partition:
            return "NO"
        return "yes" if holds else "excluded" if explained else "NO"

    prompts_seen = audit.bundled_in_window + audit.unanswered + audit.early_prompts
    raw_prompt_total = sum(raw_prompts.values())
    requests_seen = (
        audit.episode_requests + audit.unowned_requests + audit.early_requests
    )
    unowned_interrupts = sum(audit.interrupt_reasons.values())
    priced_elsewhere = [m for m in unpriced_models if rates_for(m) is not None]
    raw = ", ".join(f"{k} {n:,}" for k, n in sorted(raw_prompts.items()))
    reasons = "; ".join(
        f"{why}: {n:,}" for why, n in sorted(audit.interrupt_reasons.items())
    )
    checks = [
        (
            "every prompt turn is accounted for",
            status(
                audit.unanswered + audit.early_prompts == 0,
                prompts_seen == raw_prompt_total
                and dict(audit.prompt_turns) == raw_prompts,
                True,
            ),
            f"ledger: {raw}; in episodes {audit.bundled_in_window:,}"
            f" ({audit.bundled:,} bundled with an earlier prompt) + no request"
            f" followed {audit.unanswered:,} + in an episode begun before the window"
            f" {audit.early_prompts:,} = {prompts_seen:,} of {raw_prompt_total:,};"
            f" episodes: {len(es):,}",
        ),
        (
            "every main request is in an episode",
            status(
                audit.unowned_requests + audit.early_requests == 0,
                requests_seen == raw_requests,
                True,
            ),
            f"in episodes {audit.episode_requests:,} + before any prompt"
            f" {audit.unowned_requests:,} (${audit.unowned_cost:,.2f}) + of an"
            f" episode begun before the window {audit.early_requests:,}"
            f" (${audit.early_cost:,.2f}) = {requests_seen:,} of {raw_requests:,}",
        ),
        (
            "every agent is linked to its parent",
            status(not unlinked, not lost and not no_dispatch),
            f"agents in window: {len(in_window):,} = linked"
            f" {len(in_window) - len(unlinked) - len(lost):,} + unlinked"
            f" {len(unlinked):,} + lost {len(lost):,}; linked without a spawn time:"
            f" {len(no_dispatch):,}; spawn ids shared by several callers:"
            f" {tree.ambiguous:,}",
        ),
        (
            "every linked agent is in an episode",
            status(
                not outside,
                len(reached & set(in_window)) + len(outside) + len(unlinked)
                == len(in_window),
                not unexplained_agents,
            ),
            f"outside every episode: {len(outside):,}, of which unexplained (spawned"
            f" inside an episode, or root unknown): {len(unexplained_agents):,}",
        ),
        (
            "every interrupt is in an episode",
            status(
                not unowned_interrupts,
                audit.interrupts == raw_interrupts,
                True,
            ),
            f"{audit.interrupts:,} of {raw_interrupts:,} seen; outside every"
            f" episode: {unowned_interrupts:,} ({reasons or '-'})",
        ),
        (
            "every compaction event has a row",
            status(len(events) == raw_compactions, len(events) == raw_compactions),
            f"events in window: {raw_compactions:,}; rows: {len(events):,}"
            f" ({sum(c.overlaps for c in events):,} overlap another event,"
            f" {sum(c.partial for c in events):,} with a partial window)",
        ),
        (
            "growth and carry are null exactly across a compaction",
            status(spanning == nulled, spanning == nulled),
            f"episodes with a compaction inside: {len(spanning):,}; without growth:"
            f" {len(nulled):,}",
        ),
        (
            "every request has a price",
            status(not unpriced, True, not priced_elsewhere),
            f"unpriced requests: {unpriced:,} ({unpriced_tokens:,} tokens; models"
            f" without a catalog price: {', '.join(unpriced_models) or '-'}),"
            " counted as $0",
        ),
        (
            "episode cost adds up to the window",
            status(abs(residual) < 0.01, True),
            f"window ${window_cost:,.2f} = episodes ${in_episodes:,.2f}"
            f" + main before any prompt ${audit.unowned_cost:,.2f} + main of"
            f" earlier episodes ${audit.early_cost:,.2f} + agents outside"
            f" ${outside_cost:,.2f} + unexplained ${round(residual, 2) + 0.0:,.2f}"
            f" ({_pct(in_episodes, window_cost)} in episodes)",
        ),
    ]
    out = [
        "## Audit",
        "",
        "Each check compares a count taken straight from the ledger with what the"
        " episodes account for. yes = holds; excluded = the parts add up and every"
        " excluded item has a stated reason; NO = they do not.",
        "",
        "| check | status | detail |",
        "|---|---|---|",
    ]
    for name, ok, detail in checks:
        out.append(f"| {name} | {ok} | {detail} |")
    return out


def _compaction_lines(events: list[Compaction]) -> list[str]:
    isolated = [c for c in events if not c.partial and not c.overlaps]
    pooled = {"before": Window(), "after": Window()}
    for c in isolated:
        pooled["before"].add(c.before)
        pooled["after"].add(c.after)
    k = COMPACTION_K
    out = [
        "",
        f"## Around compaction ({len(events):,} compaction events, one row each)",
        "",
        f"Each event compares the {k} main requests of its thread before it with the"
        f" {k} after it (any episode kind). Reads, edits and shell runs are matched"
        " inside each window. A window with fewer requests is partial; events whose"
        " windows share a request with another event of the same thread overlap."
        f" Full windows, no overlap: {len(isolated):,}; partial:"
        f" {sum(c.partial for c in events):,}; overlapping:"
        f" {sum(c.overlaps for c in events):,}. Cells read before -> after.",
        "",
        f"Pooled over the {len(isolated):,} events with full windows and no overlap:",
        "",
        "| side | requests | " + WINDOW_HEAD + " |",
        "|---|---" + "|---" * 10 + "|",
    ]
    for side, w in pooled.items():
        out.append(f"| {side} | {len(w.prompts):,} | " + " | ".join(w.cells()) + " |")
    out += [
        "",
        "| event | thread | requests before/after | " + WINDOW_HEAD + " | partial"
        " | overlaps |",
        "|---|---|---" + "|---" * 11 + "|",
    ]
    for i, c in enumerate(events, 1):
        cells = [
            f"{b} -> {a}"
            for b, a in zip(c.before.cells(), c.after.cells(), strict=True)
        ]
        out.append(
            f"| {i} | {c.thread[:8]} | {len(c.before.prompts)}/{len(c.after.prompts)}"
            f" | " + " | ".join(cells) + f" | {'yes' if c.partial else '-'}"
            f" | {'yes' if c.overlaps else '-'} |"
        )
    return out


def render(conn: sqlite3.Connection, days: int = 30) -> str:
    start = _window_start(conn, days)
    if start is None:
        return "no episodes in the window\n"
    tree = Tree(conn)
    audit = Audit()
    es = episodes(conn, days, tree, audit)
    if not es:
        return "no episodes in the window\n"
    events = compactions(conn, days)
    human = [e for e in es if e.source == "human"]
    sources = sorted({e.source for e in es})
    lines = [
        f"# Episode report ({days} days to {max(e.reqs[-1].ts_last for e in es)[:10]})",
        "",
        "Episode = a prompt turn in a main thread (prompts no request answered in"
        " between are one bundle), its main requests until the next prompt, and every"
        " agent spawned from them, nested ones included. Tables below use human"
        " episodes; sdk and scheduled ones are counted here and in the row file.",
        f"inline = no agent; delegated = agents and <= {OWN_TOOLS_DELEGATED} other main"
        " tool calls; mixed = agents and more. Band = main prompt at the episode's"
        " first request. $ = ledger cost_usd.",
        "Growth = last prompt + last answer - first prompt. Carry = growth x each later"
        " main request's cache-read price until compaction or session end: an exposure"
        " estimate, not a measured cost. An episode with a compaction inside it has no"
        f" growth and no carry (null): {len([e for e in human if e.growth is None]):,}"
        f" of {len(human):,} human episodes, left out of the medians below.",
        "Attention signals are weak proxies (shares of main tool results, of Reads and"
        " of episodes), confounded by job and tool mix. No verdict on delegation.",
        "Exact Edit reversal = an Edit that undoes an earlier Edit of the same file in"
        " the episode, out of Edit calls only (Write keeps a one-way hash, MultiEdit"
        " one hash of its whole list). Rework after agents is two signals: main"
        " mutations (Edit, MultiEdit, Write) and main Reads, each after the last agent"
        " of the episode finished, on a file an agent of that episode read or wrote.",
        "Shell failure is the tool result's is_error flag. The transcripts carry no"
        " structured exit status: user records have no exit code under toolUseResult"
        " (Bash/PowerShell results hold stdout, stderr, interrupted and a few hints"
        " only), so the ledger has none and `failed cmd failed again` rests on"
        " is_error.",
        "",
        *_audit_lines(conn, start, es, tree, audit, events),
        "",
        "Episodes by source: "
        + ", ".join(
            f"{s} {sum(e.source == s for e in es):,}"
            f" (${sum(e.total for e in es if e.source == s):,.2f})"
            for s in sources
        ),
        "",
        "## Cost by kind and band (human episodes)",
        "",
        "| kind | band | episodes | median main requests | median growth"
        " | median main $ | median agents $ | median carry $ | median total $"
        " | total $ | cache misses | miss $ | compaction rewrites |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    groups: dict[tuple[str, str], list[Episode]] = defaultdict(list)
    for e in human:
        groups[(e.kind, band(e.start))].append(e)
    for kind in KINDS:
        for b in BAND_ORDER:
            g = groups.get((kind, b), [])
            if not g:
                continue
            lines.append(
                f"| {kind} | {b} | {len(g):,} | {_med([len(e.reqs) for e in g]):,.0f}"
                f" | {_fmt(_med_known([e.growth for e in g]), ',.0f')}"
                f" | {_med([e.main_cost for e in g]):.2f}"
                f" | {_med([e.sub_inclusive for e in g]):.2f}"
                f" | {_fmt(_med_known([e.carry_cost for e in g]), '.2f')}"
                f" | {_med([e.total for e in g]):.2f}"
                f" | {sum(e.total for e in g):,.2f}"
                f" | {sum(e.misses for e in g):,}"
                f" | {sum(e.miss_cost for e in g):,.2f}"
                f" | {sum(e.compaction_rewrites for e in g):,} |"
            )

    with_agents = [e for e in human if e.roots]
    lines += [
        "",
        "## Where the money goes in episodes with agents (human)",
        "",
        "| kind | episodes | parallel (>1 in one request) | main before dispatch $"
        " | main while agents run $ | main between agents $ | main after agents $"
        " | agents direct $ | agents incl. nested $ | median wait min"
        " | median agent-active min |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for kind in ("delegated", "mixed"):
        g = [e for e in with_agents if e.kind == kind]
        if g:
            lines.append(
                f"| {kind} | {len(g):,} | {sum(e.max_parallel > 1 for e in g):,}"
                f" | {sum(e.main_before for e in g):,.2f}"
                f" | {sum(e.main_during for e in g):,.2f}"
                f" | {sum(e.main_gap for e in g):,.2f}"
                f" | {sum(e.main_after for e in g):,.2f}"
                f" | {sum(e.sub_exclusive for e in g):,.2f}"
                f" | {sum(e.sub_inclusive for e in g):,.2f}"
                f" | {_med([e.wait_s / 60 for e in g]):.1f}"
                f" | {_med([e.active_s / 60 for e in g]):.1f} |"
            )

    lines += [
        "",
        "## Attention signals by band (human episodes, weak proxies)",
        "",
        "| kind | band | episodes" + SIGNAL_HEAD,
        "|---|---|---" + SIGNAL_RULE + "|",
    ]
    for kind in KINDS:
        for b in BAND_ORDER:
            g = groups.get((kind, b), [])
            if g:
                lines.append(f"| {kind} | {b} | {len(g):,}" + _signals(g))

    events = compactions(conn, days)
    lines += _compaction_lines(events)

    nested = [e for e in human if e.nested]
    lines += [
        "",
        "## Nested agents (human episodes)",
        "",
        f"Episodes with nested agents: {len(nested):,}; descendants:"
        f" {sum(e.nested for e in nested):,}. Agents $ direct only:"
        f" {sum(e.sub_exclusive for e in nested):,.2f}, with descendants:"
        f" {sum(e.sub_inclusive for e in nested):,.2f}.",
        "",
    ]
    return "\n".join(lines)
