"""Agent placement report (placement/when-to-agent.md, part 2).

Reads subagent threads from the ledger and an agent catalog (the definitions in
~/.claude/agents and which agents skills name), applies fixed rules, and prints which
general-purpose work could go to an existing agent, which recurring jobs are worth a
pilot agent, and which existing agents to retire, merge, audit or keep. No model
judgment: the same ledger and catalog give byte-identical output. Prompts and tool
targets are never printed; only counts, tool names, token sums and call descriptions.

A job is what the agent did, not what its description says: the areas it wrote to
(site areas from the rules file, code, scratch, ~/.claude, other files), or, when it
wrote nothing, the area it read most; external systems it used count either way.
Descriptions only label examples.

Limits the report states rather than hides: shell reads (cat, sed) and Grep/Glob paths
are not seen, so a read-only job is known by its Read calls; the catalog is today's
files (the drift column shows where history differs); no number here proves an agent
beats inline work, so new agents are piloted and measured, never credited with a
saving up front.
"""

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import median

from tokenlab.rules import DEFAULT, Rules

GENERIC = frozenset({"general-purpose", "fork"})
# Meta tools every thread may call; they say nothing about the work.
IGNORED_TOOLS = frozenset(
    {
        "ToolSearch",
        "TodoWrite",
        "TaskCreate",
        "TaskUpdate",
        "TaskList",
        "TaskGet",
        "TaskOutput",
        "TaskStop",
        "SubagentHandback",
        "SendMessage",
        "ListAgents",
        "Monitor",
        "ScheduleWakeup",
        "Skill",
    }
)
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
SHELL_TOOLS = frozenset({"Bash", "PowerShell", "bash"})
WEB_TOOLS = frozenset({"WebSearch", "WebFetch"})
SHELL_WRITE = re.compile(
    r"(?:^|[\s;&|])(?:>>?|tee\b|sed -i|Set-Content|Add-Content|Out-File|"
    r"git (?:commit|push|apply)|rm\s|mv\s|cp\s)",
    re.IGNORECASE,
)
# A file with one of these extensions, outside the areas below, is code.
CODE_EXT = frozenset(
    "cs csproj sln slnx props targets py ts tsx js mjs cjs ps1 psm1 sh sql".split()
)
# Where a file lives, first match wins (site areas from Rules.areas come first);
# else code:<ext> (CODE_EXT) or a file.
AREAS = (
    ("claude-config", "/.claude/"),
    ("scratch", "/scratchpad/"),
    ("scratch", "/appdata/local/temp/"),
    ("scratch", "$temp"),
    ("scratch", "$tmp"),
    ("scratch", "/tmp/"),  # noqa: S108 - a path to classify, not a temp file
)
# Ties between areas go to the one that names the job best; scratch is a means only.
AREA_RANK = ("code", "claude-config", "file", "scratch")
SHELL_HTTP = re.compile(r"\bcurl\b|Invoke-(?:RestMethod|WebRequest)\b", re.I)
NO_TRACE = "- → -"  # read nothing via Read, wrote nothing seen: work done in a shell
NULL_DEVICES = frozenset({"/dev/null", "nul", "$null"})
ABS_PATH = re.compile(r"(?:\b[A-Za-z]:[\\/]|(?<![\w.:/~$*|#])/)[^\s;&|'\"<>]+")
REDIRECT = re.compile(r"(?:^|[^\d&])>>?\s*([^\s;&|>]+)")


@dataclass(frozen=True)
class AgentDef:
    name: str
    tools: frozenset[str] | None  # None: no `tools` line, inherits everything
    model: str | None


@dataclass
class Call:
    thread_id: str
    agent: str
    session: str
    first_ts: str
    tools: frozenset[str]
    externals: frozenset[str]  # external systems (jira, db, ...) and web
    read_area: str | None  # area read most by Read calls, None when none
    output: str | None  # area the output went to (output_area), None: wrote nothing
    first_tokens: int
    total_tokens: int
    final_text: int  # last request's output minus thinking: the returned answer
    first_read: int  # cache-read tokens of the first request
    total_read: int  # cache-read tokens over the whole thread
    total_prompt: int  # input + cache read + cache write over the whole thread
    first_cost: float  # USD of the first request (ledger price incl. cache rates)
    total_cost: float  # USD of the whole thread
    errors: tuple[str, ...]  # tool of each result that came back as an error, sorted
    description: str
    signature: str = field(init=False)

    def __post_init__(self) -> None:
        reads = sorted(self.externals)
        if not self.output and self.read_area:
            reads.append(self.read_area)
        self.signature = f"{'+'.join(reads) or '-'} → {self.output or '-'}"

    @property
    def errored(self) -> bool:
        return bool(self.errors)

    @property
    def day(self) -> str:
        return self.first_ts[:10]


def area(path: str | None, rules: Rules = DEFAULT) -> str:
    low = "/" + (path or "").lower().replace("\\", "/")
    found = next((name for name, part in (*rules.areas, *AREAS) if part in low), None)
    if found:
        return found
    name = low.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else ""
    return f"code:{ext}" if ext in CODE_EXT else "file"


def _shell_areas(command: str, rules: Rules) -> list[str]:
    """Named areas a writing shell command points at: redirects and absolute paths.

    A job that writes through a script in scratch still names where it works
    (`cd /c/Repos/specs/x; python $TEMP/gen.py`). A plain file is left out:
    from a shell it is mostly a log (`dotnet test > log.txt`), not the output.
    """
    dests = [d for d in REDIRECT.findall(command) if d.lower() not in NULL_DEVICES]
    areas = (area(p, rules) for p in dests + ABS_PATH.findall(command))
    return [a for a in areas if a != "file"]


def output_area(
    used: list[tuple[str, str | None]], rules: Rules = DEFAULT
) -> str | None:
    """The area a job writes its output to: the one written most outside scratch.

    Native writes decide; shell writes count only when no native write left scratch.
    """
    rank = (*(name for name, _ in rules.areas), *AREA_RANK)
    native = Counter(area(t, rules) for name, t in used if name in WRITE_TOOLS)
    shell: Counter[str] = Counter()
    for name, target in used:
        if name in SHELL_TOOLS and target and SHELL_WRITE.search(target):
            shell.update(_shell_areas(target, rules))
    for counts in (native, shell):
        real = {a: n for a, n in counts.items() if a != "scratch"}
        if real:
            return min(real, key=lambda a: (-real[a], rank.index(a.split(":")[0]), a))
    return "scratch" if native or shell else None


def read_area(used: list[tuple[str, str | None]], rules: Rules = DEFAULT) -> str | None:
    counts = Counter(area(t, rules) for name, t in used if name == "Read")
    return min(counts, key=lambda a: (-counts[a], a)) if counts else None


def externals(
    used: list[tuple[str, str | None]], rules: Rules = DEFAULT
) -> frozenset[str]:
    """External systems a job read: MCP servers, the web, and HTTP APIs via a shell."""
    found = {
        rules.external.get(n.split("__")[1], n.split("__")[1])
        for n, _ in used
        if n.startswith("mcp__")
    }
    if any(n in WEB_TOOLS for n, _ in used):
        found.add("web")
    if any(n in SHELL_TOOLS and t and SHELL_HTTP.search(t) for n, t in used):
        found.add("http")
    return frozenset(found)


def parse_agent(path: Path) -> AgentDef:
    text = path.read_text(encoding="utf-8")
    front = text.split("---", 2)[1] if text.startswith("---") else ""
    fields = dict(
        (k.strip(), v.strip())
        for k, _, v in (line.partition(":") for line in front.splitlines())
        if v
    )
    tools = fields.get("tools")
    parsed = (
        frozenset(t.strip().split("(")[0] for t in tools.split(",") if t.strip())
        if tools
        else None
    )
    return AgentDef(fields.get("name", path.stem), parsed, fields.get("model"))


def load_catalog(agents_dir: Path) -> dict[str, AgentDef]:
    return {a.name: a for a in map(parse_agent, sorted(agents_dir.glob("*.md")))}


def skill_refs(skills_dir: Path, names: list[str]) -> frozenset[str]:
    """Agent names that any live skill file mentions."""
    found: set[str] = set()
    for f in sorted(skills_dir.rglob("*.md")):
        if "_retired" in f.parts:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        found.update(n for n in names if re.search(rf"\b{re.escape(n)}\b", text))
    return frozenset(found)


def calls(conn: sqlite3.Connection, days: int, rules: Rules = DEFAULT) -> list[Call]:
    end = conn.execute("SELECT MAX(ts_last) FROM requests").fetchone()[0]
    if end is None:
        return []
    start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """
        SELECT t.thread_id, COALESCE(t.agent_type, '?'), t.session_id, t.description,
               MIN(r.ts_first),
               SUM(CASE WHEN r.seq = 0 THEN r.input + r.cache_read + r.cache_write
                        ELSE 0 END),
               SUM(r.input + r.cache_read + r.cache_write + r.output),
               (SELECT r2.output - r2.thinking FROM requests r2
                WHERE r2.thread_id = t.thread_id ORDER BY r2.seq DESC LIMIT 1),
               SUM(CASE WHEN r.seq = 0 THEN r.cache_read ELSE 0 END),
               SUM(r.cache_read),
               SUM(r.input + r.cache_read + r.cache_write),
               SUM(CASE WHEN r.seq = 0 THEN COALESCE(r.cost_usd, 0) ELSE 0 END),
               SUM(COALESCE(r.cost_usd, 0))
        FROM threads t JOIN requests r ON r.thread_id = t.thread_id
        WHERE t.kind = 'subagent'
        GROUP BY t.thread_id
        HAVING MIN(r.ts_first) >= ?
        ORDER BY t.thread_id
        """,
        (start.replace("+00:00", "Z"),),
    ).fetchall()
    # One pass over `content` (it has no index on thread_id).
    wanted = {r[0] for r in rows}
    used_by: dict[str, list[tuple[str, str | None]]] = defaultdict(list)
    errors_by: dict[str, list[str]] = defaultdict(list)
    for tid, kind, name, target, is_error in conn.execute(
        "SELECT thread_id, kind, tool, target, is_error FROM content"
    ):
        if tid not in wanted:
            continue
        if kind == "tool_use" and name:
            used_by[tid].append((str(name), target))
        elif kind == "tool_result" and is_error:
            errors_by[tid].append(str(name or "?"))
    out = []
    for (
        tid,
        agent,
        session,
        desc,
        first_ts,
        first_tok,
        total_tok,
        final,
        first_read,
        total_read,
        total_prompt,
        first_cost,
        total_cost,
    ) in rows:
        used = used_by.get(tid, [])
        tools = frozenset(n for n, _ in used if n not in IGNORED_TOOLS)
        out.append(
            Call(
                tid,
                agent,
                session,
                first_ts,
                tools,
                externals(used, rules),
                read_area(used, rules),
                output_area(used, rules),
                first_tok,
                total_tok,
                max(0, final or 0),
                first_read,
                total_read,
                total_prompt,
                first_cost,
                total_cost,
                tuple(sorted(errors_by.get(tid, []))),
                desc or "",
            )
        )
    return out


def _med(xs: list[int]) -> int:
    return int(median(xs)) if xs else 0


def _medf(xs: list[float]) -> float:
    return float(median(xs)) if xs else 0.0


def _share(part: int, whole: int) -> str:
    return f"{part / whole:.0%}" if whole else "-"


def _spread(g: list[Call]) -> tuple[int, int]:
    return len({c.session for c in g}), len({c.day for c in g})


def _job(g: list[Call]) -> str | None:
    """An agent's job: the signature most of its calls share.

    None when it has no calls, or when most leave no visible trace (shell only):
    such work cannot be matched to or merged with anything.
    """
    counts = Counter(c.signature for c in g)
    if not counts:
        return None
    job = min(counts, key=lambda j: (-counts[j], j))
    return None if job == NO_TRACE else job


def _model(output: str | None) -> str:
    return "sonnet" if output else "haiku"


def _short(tool: str) -> str:
    return tool.rsplit("__", 1)[-1] if tool.startswith("mcp__") else tool


def render(
    conn: sqlite3.Connection,
    catalog: dict[str, AgentDef],
    referenced: frozenset[str],
    days: int = 30,
    rules: Rules = DEFAULT,
) -> str:
    long = calls(conn, max(days, rules.retire_days), rules)
    if not long:
        return "no subagent threads in the window\n"
    end = max(c.first_ts for c in long)
    start = (datetime.fromisoformat(end) - timedelta(days=days)).isoformat()
    cs = [c for c in long if c.first_ts >= start.replace("+00:00", "Z")]
    by_agent: dict[str, list[Call]] = defaultdict(list)
    for c in cs:
        by_agent[c.agent].append(c)
    lines = [
        f"# Agent placement report ({days} days to {end[:10]})",
        "",
        f"Recurring = >= {rules.min_recur} calls over >= {rules.min_sessions}"
        f" sessions and >= {rules.min_days} days."
        " First = first-request prompt tokens; total = whole thread incl. output."
        " Final answer = last request's output minus thinking.",
        "$ = ledger cost_usd: input, cache reads, 5m/1h cache writes and output, each"
        " at its own catalog price. Cache read % = cache-read share of prompt tokens.",
        "No saving is claimed for a new agent: pilot it, then rerun this report.",
        "",
        "## Calls by agent",
        "",
        "| agent | calls | sessions | median first | first cache read %"
        " | median first $ | total tokens | thread cache read % | total $"
        " | median final answer | threads with a tool error |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for name in sorted(by_agent, key=lambda n: (-len(by_agent[n]), n)):
        g = by_agent[name]
        lines.append(
            f"| {name} | {len(g)} | {_spread(g)[0]}"
            f" | {_med([c.first_tokens for c in g]):,}"
            f" | {_share(sum(c.first_read for c in g), sum(c.first_tokens for c in g))}"
            f" | {_medf([c.first_cost for c in g]):.3f}"
            f" | {sum(c.total_tokens for c in g):,}"
            f" | {_share(sum(c.total_read for c in g), sum(c.total_prompt for c in g))}"
            f" | {sum(c.total_cost for c in g):,.2f}"
            f" | {_med([c.final_text for c in g]):,}"
            f" | {sum(c.errored for c in g)} |"
        )

    # 1. Generic calls an existing agent could have taken.
    lines += ["", "## Generic calls an existing agent covers", ""]
    generic = [c for c in cs if c.agent in GENERIC]
    rest: list[Call] = []
    misrouted: dict[str, list[Call]] = defaultdict(list)
    skipped: Counter[str] = Counter()
    # An agent's job is what its own calls did; an agent with no calls has none yet.
    long_by: dict[str, list[Call]] = defaultdict(list)
    for c in long:
        long_by[c.agent].append(c)
    job = {name: _job(long_by.get(name, [])) for name in sorted(catalog)}
    for c in generic:
        same = [a for a in catalog.values() if job[a.name] == c.signature]
        fits = [a for a in same if a.tools is None or c.tools <= a.tools]
        if fits:
            best = min(fits, key=lambda a: (-len(by_agent.get(a.name, [])), a.name))
            misrouted[best.name].append(c)
        else:
            if same:
                skipped["same job, but used tools the agent lacks"] += 1
            rest.append(c)
    if misrouted:
        lines += [
            "| should go to | calls | sessions | median first now | agent median"
            " | first-request upper bound | median first $ now | agent median $"
            " | $ upper bound |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for name in sorted(misrouted):
            g = misrouted[name]
            own = _med([c.first_tokens for c in by_agent.get(name, [])])
            now = _med([c.first_tokens for c in g])
            bound = f"{len(g) * max(0, now - own):,}" if own else "no agent calls"
            own_usd = _medf([c.first_cost for c in by_agent.get(name, [])])
            now_usd = _medf([c.first_cost for c in g])
            usd = f"{len(g) * max(0.0, now_usd - own_usd):.2f}" if own else "-"
            lines.append(
                f"| {name} | {len(g)} | {_spread(g)[0]} | {now:,} | {own:,} | {bound}"
                f" | {now_usd:.3f} | {own_usd:.3f} | {usd} |"
            )
    else:
        lines.append("none")
    for why, n in sorted(skipped.items()):
        lines.append(f"Not routed, {why}: {n}")

    # 2. Recurring generic jobs no agent covers.
    clusters: dict[str, list[Call]] = defaultdict(list)
    for c in rest:
        clusters[c.signature].append(c)
    lines += [
        "",
        "## Recurring generic jobs (pilot candidates, most sessions first)",
        "",
        "Job = reads → writes; `- → -` = shell-only work, no visible job."
        " Examples = the most frequent call descriptions. Tool errors = results"
        " that came back as an error; read them before writing the agent.",
        "",
        "| job | calls | sessions | days | median first"
        " | median first $ | total tokens | total $ | tools used (calls)"
        " | tool errors (calls) | model | examples |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    left: Counter[str] = Counter()
    left_tokens: Counter[str] = Counter()
    left_usd: dict[str, float] = defaultdict(float)
    for sig, g in sorted(
        clusters.items(), key=lambda kv: (-_spread(kv[1])[0], -len(kv[1]), kv[0])
    ):
        sessions, ndays = _spread(g)
        if (
            len(g) < rules.min_recur
            or sessions < rules.min_sessions
            or ndays < rules.min_days
        ):
            why = "not recurring"
        else:
            counts = Counter(t for c in g for t in c.tools)
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[
                : rules.top_tools
            ]
            descs = Counter(c.description for c in g if c.description)
            examples = sorted(descs.items(), key=lambda kv: (-kv[1], kv[0]))
            examples = examples[: rules.top_examples]
            errs = Counter(t for c in g for t in c.errors)
            worst = sorted(errs.items(), key=lambda kv: (-kv[1], kv[0]))[
                : rules.top_tools
            ]
            lines.append(
                f"| {sig} | {len(g)} | {sessions} | {ndays}"
                f" | {_med([c.first_tokens for c in g]):,}"
                f" | {_medf([c.first_cost for c in g]):.3f}"
                f" | {sum(c.total_tokens for c in g):,}"
                f" | {sum(c.total_cost for c in g):,.2f}"
                f" | {', '.join(f'{_short(t)} {n}' for t, n in top)}"
                f" | {', '.join(f'{_short(t)} {n}' for t, n in worst) or 'none'}"
                f" | {_model(g[0].output)}"
                f" | {', '.join(f'{d} ({n})' for d, n in examples)} |"
            )
            continue
        left[why] += len(g)
        left_tokens[why] += sum(c.total_tokens for c in g)
        left_usd[why] += sum(c.total_cost for c in g)
    lines.append("")
    for why in sorted(left):
        lines.append(
            f"Stays generic, {why}: {left[why]} calls,"
            f" {left_tokens[why]:,} total tokens, ${left_usd[why]:,.2f}"
        )

    # 3. Existing agents, judged on the longer window.
    lines += [
        "",
        f"## Existing agents ({max(days, rules.retire_days)} days)",
        "",
        "| agent | calls | sessions | days | verdict | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for name in sorted(catalog):
        a, g = catalog[name], long_by.get(name, [])
        sessions, ndays = _spread(g)
        used = set().union(*(c.tools for c in g)) if g else set()
        verdicts = []
        if len(g) <= rules.max_uses_to_retire:
            where = (
                "; a skill names it: fold into that skill" if name in referenced else ""
            )
            verdicts.append(("retire", f"calls: {len(g)}{where}"))
        elif len(g) < rules.min_recur or sessions < rules.min_sessions:
            verdicts.append(
                ("justify or retire", "rare: keep only as a stated exception")
            )
        twins = [
            n
            for n in sorted(catalog)
            if n != name
            and job[name]
            and job[n] == job[name]
            and catalog[n].model == a.model
        ]
        if twins:
            verdicts.append(("merge", "same job as " + ", ".join(twins)))
        drift = sorted(used - a.tools) if a.tools is not None else []
        if drift:
            verdicts.append(
                ("definition drift", "used, not granted now: " + ", ".join(drift))
            )
        elif len(g) >= rules.min_uses_to_narrow and a.tools is not None:
            idle = sorted(_short(t) for t in a.tools - used)
            if idle:
                verdicts.append(("check grants", "never used: " + ", ".join(idle)))
        bad = sum(c.errored for c in g)
        if len(g) >= rules.min_recur and bad / len(g) > rules.error_share:
            verdicts.append(
                ("audit errors", f"{bad}/{len(g)} threads had a tool error")
            )
        res = _med([c.final_text for c in g])
        if res > rules.result_cap:
            verdicts.append(("tighten contract", f"median answer {res:,} tokens"))
        if not verdicts:
            verdicts.append(("keep", "recurring, no rule fired"))
        for verdict, why in verdicts:
            lines.append(
                f"| {name} | {len(g)} | {sessions} | {ndays} | {verdict} | {why} |"
            )

    lines.append("")
    return "\n".join(lines)
