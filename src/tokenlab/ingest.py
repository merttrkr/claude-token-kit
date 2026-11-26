"""Transcripts -> ledger.

Rebuilds the database from scratch so the result depends only on the corpus.
"""

import hashlib
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tokenlab import ledger, otel
from tokenlab.prices import request_cost

# The first key present names what a tool call acted on.
TARGET_KEYS = (
    "file_path",
    "notebook_path",
    "command",
    "pattern",
    "url",
    "query",
    "skill",
    "subagent_type",
    "path",
    "to",
)
TARGET_MAX = 500
# System records that mark a model request the transcript does not show as an
# assistant message.
EVENT_SUBTYPES = frozenset({"compact_boundary", "away_summary"})
# Record types seen in Claude Code transcripts (2.1.3xx). Those not read here
# carry no tokens; a type outside this set is counted, so a format change shows
# up in the report instead of silently lowering its numbers.
KNOWN_TYPES = frozenset(
    {
        "assistant",
        "user",
        "attachment",
        "system",
        "api-request-shape",
        "cost-state",
        "api-request",
        "api-request-blob",
        "last-prompt",
        "atis-latch",
        "mode",
        "ai-title",
        "session-owner",
        "queue-operation",
        "permission-mode",
        "agent-name",
        "custom-title",
        "summary",
        "file-history-snapshot",
        "file-history-delta",
        "tools-used",
        "bridge-session",
        "agent-setting",
        "artifact-autoreact-ledger",
        "artifact-comment-monitor",
        "frame-link",
        "fork-context-ref",
    }
)
# How a user-role record that is not a tool result is classified. Its text is read
# here and never stored.
INTERRUPT = "[Request interrupted by user"
TURN_PREFIXES = (
    ("<task-notification", "task_notification"),
    ("<bash-input", "command"),
    ("<bash-stdout", "command"),
    ("<local-command", "command"),
    ("Another Claude session sent", "peer"),
)
# A slash command (skill, plugin skill or built-in) as the user message records it.
SLASH_COMMAND = re.compile(r"<command-name>/([^<\s]+)</command-name>")
# A tool error's kind from a fixed list, never its text: an error message can hold
# secrets or other people's words. First match wins. On 472 errors in the
# maintainer's transcripts (2026-11) 28% fell into "other", about a third of them
# the user's rejections. Hook blocks and the
# user's rejections are in `decision` already.
ERROR_KINDS = [
    ("file not read first", re.compile(r"has not been read yet", re.I)),
    ("file changed since read", re.compile(r"modified since .{0,20}read", re.I)),
    ("edit text not found", re.compile(r"string to replace not found", re.I)),
    (
        "http",
        re.compile(r"\b(?:status(?: code)?|http(?:/[\d.]+)?):? ([45])\d\d\b", re.I),
    ),
    (
        "file not found",
        re.compile(
            r"no such file|(?:file|path|directory) does not exist|enoent"
            r"|cannot find the (?:path|file)",
            re.I,
        ),
    ),
    (
        "permission denied",
        re.compile(r"permission denied|access is denied|eacces", re.I),
    ),
    (
        "command not found",
        re.compile(
            r"command not found|is not recognized as (?:the name of a cmdlet"
            r"|an internal or external command)",
            re.I,
        ),
    ),
    ("timeout", re.compile(r"timed? ?out", re.I)),
    (
        "too large",
        re.compile(r"exceeds (?:the )?(?:maximum|max|limit)|too large|too long", re.I),
    ),
    (
        "invalid input",
        re.compile(r"inputvalidationerror|invalid (?:input|param)", re.I),
    ),
    ("exit code", re.compile(r"exit code \d{1,3}\b", re.I)),
]
# The tools an agent listing line names at its end: "- name: … (Tools: Read, Grep)".
AGENT_TOOLS = re.compile(r"\(Tools: ([^()]*)\)\s*$")
ORIGIN_KINDS = frozenset({"human", "peer", "task_notification"})
# A human turn that opens like this, or says one of the CORRECTION_ANY phrases, is
# counted as a correction. A rough proxy, read together with interrupts and
# rejections, never alone. Measured on a month of real Turkish/English turns: the
# opening words alone caught 17 corrections; with the wider set and the phrases, 41,
# and a sample of the new ones were all corrections or objections. A bare "yanlış"
# inside a sentence is left out: it mostly described a bug, not Claude's mistake.
CORRECTION = re.compile(
    r"^\W*(?:hayır|hayir|yok\b|yanlış|yanlis|olmadı|olmadi|olmamış|olmamis|"
    r"öyle değil|oyle degil|böyle değil|boyle degil|bu değil|değil\b(?! mi)|"
    r"dur\b|durdur|bekle\b|geri al|yapma\b|hatalı|hatali|no\b|nope\b|wrong\b|"
    r"stop\b|wait\b|don'?t\b|undo\b|revert\b|incorrect\b|that'?s not|not what|"
    r"that'?s wrong)",
    re.IGNORECASE,
)
CORRECTION_ANY = re.compile(
    r"\bsana\b.{0,40}\b(?:dedim|demiştim|demistim|söyledim|söylemiştim|soylemistim)\b|"
    r"\bben\b.{0,30}\b(?:dedim|demiştim|istemedim|sormadım)\b|"
    r"\bbunu (?:istemedim|sormadım|sormadim|demedim)\b|"
    r"\b(?:neden|niye|niçin)\b.{0,60}\b(?:yaptın|yaptin|ettin|sildin|değiştirdin|"
    r"degistirdin|ekledin|yazdın|yazdin)\b|"
    r"\b(?:yapmadın|yapmadin|okumadın|okumadin|bakmadın|bakmadin)\b|"
    r"hoşuma gitmedi|hosuma gitmedi|beğenmedim|begenmedim|"
    r"yanlış anladın|yanlis anladin|\banlamadın\b|\banlamadin\b|"
    r"\bI (?:said|told you)\b|\bwhy did you\b|\byou (?:didn'?t|did not)\b|"
    r"\bnot what I (?:asked|meant|said)\b|\bI didn'?t ask\b",
    re.IGNORECASE,
)


def is_correction(text: str) -> bool:
    return bool(CORRECTION.match(text) or CORRECTION_ANY.search(text))


HOOK_ERROR = re.compile(r"^(?:Pre|Post)ToolUse:\S+ hook error")
GATE_ID = re.compile(r"\[(G\d+)[:\]]")
USER_REFUSAL = "The user doesn't want to proceed"


def _pairs(a: dict[str, Any], names: str, texts: str) -> list[tuple[str, int]]:
    ns, ts_ = a.get(names) or [], a.get(texts) or []
    return [
        (str(n), len(t)) for n, t in zip(ns, ts_, strict=False) if isinstance(t, str)
    ]


def _path(p: Any) -> str:
    """A Windows path is one file whatever its slashes: `C:\\r\\x.md` → `C:/r/x.md`."""
    s = str(p)
    return s.replace("\\", "/") if re.match(r"^[A-Za-z]:[\\/]", s) else s


def error_kind(text: str) -> str:
    """`exit code 1`, `http 4xx`, `file not found`, … or `other`: from `ERROR_KINDS`."""
    for name, pattern in ERROR_KINDS:
        if m := pattern.search(text):
            if name == "exit code":
                return m.group(0).lower()
            return f"http {m.group(1)}xx" if name == "http" else name
    return "other"


def agent_tools(a: dict[str, Any]) -> list[tuple[str, str]]:
    """(agent, tool) from each agent listing line's `(Tools: …)` tail, names only.
    `*` and `All tools except …` give none: that agent has what the session has."""
    if a.get("type") != "agent_listing_delta":
        return []
    out: list[tuple[str, str]] = []
    for name, line in zip(
        a.get("addedTypes") or [], a.get("addedLines") or [], strict=False
    ):
        m = AGENT_TOOLS.search(line) if isinstance(line, str) else None
        if m and m.group(1).strip() != "*" and not m.group(1).startswith("All tools"):
            out += [(str(name), t.strip()) for t in m.group(1).split(",") if t.strip()]
    return out


def parts(a: dict[str, Any]) -> list[tuple[str, str, int]]:
    """(kind, name, chars) for each file, skill, agent, server or tool an attachment
    carries; [] for kinds that carry one thing. Chars are the part's own text, so
    they sum to a little less than the rendered attachment."""
    kind = a.get("type")
    if kind == "instructions":
        return [
            ("file", _path(f.get("path")), len(f.get("content") or ""))
            for f in a.get("files") or []
            if isinstance(f, dict)
        ]
    if kind == "file":
        # A file Claude Code attached on its own or from an @-mention: it reached
        # the context. A read signal only; its cost is not split out here.
        return [("attached", _path(a.get("filename")), text_len(a.get("content")))]
    if kind == "skill_listing":
        # "- name: description"; a plugin skill's name has its own colon
        # ("- plug:gamma: ..."), so the name ends at the first ": ".
        lines = str(a.get("content") or "").split("\n")
        return [
            ("skill", line[2:].split(": ", 1)[0].rstrip(":"), len(line))
            for line in lines
            if line.startswith("- ")
        ]
    if kind == "agent_listing_delta":
        return [("agent", n, c) for n, c in _pairs(a, "addedTypes", "addedLines")]
    if kind == "mcp_instructions_delta":
        return [("mcp", n, c) for n, c in _pairs(a, "addedNames", "addedBlocks")]
    if kind == "deferred_tools_delta":
        return [("deferred", n, c) for n, c in _pairs(a, "addedNames", "addedLines")]
    if kind == "nested_memory":
        body = a.get("content")
        text = body.get("content") if isinstance(body, dict) else body
        return [("rule", _path(a.get("path")), len(text or ""))]
    if kind == "hook_additional_context":
        body = a.get("content") or []
        text = sum(len(x) for x in body if isinstance(x, str))
        return [("hook", str(a.get("hookName") or a.get("hookEvent") or "hook"), text)]
    if kind == "invoked_skills":
        return [
            ("skill_body", str(s.get("name")), len(s.get("content") or ""))
            for s in a.get("skills") or []
            if isinstance(s, dict)
        ]
    return []


def text_len(x: Any) -> int:
    """Characters of text the model sees in a content value; image and metadata
    fields count zero."""
    if isinstance(x, str):
        return len(x)
    if isinstance(x, list):
        return sum(text_len(i) for i in x)
    if isinstance(x, dict):
        return text_len(x.get("text")) + text_len(x.get("content"))
    return 0


def plain(x: Any, limit: int = 400) -> str:
    """The leading text of a content value, for classification only."""
    if isinstance(x, str):
        return x[:limit]
    if isinstance(x, list):
        return " ".join(plain(i, limit) for i in x)[:limit]
    if isinstance(x, dict):
        return plain(x.get("text") or x.get("content"), limit)
    return ""


def turn_source(rec: dict[str, Any]) -> str:
    return "/".join(
        str(rec.get(k) or "-") for k in ("turnOrigin", "promptSource", "entrypoint")
    )


def turn_kind(rec: dict[str, Any], text: str) -> str:
    head = text.lstrip()
    if rec.get("isCompactSummary"):
        return "compact_summary"
    if head.startswith(INTERRUPT):
        return "interrupt"
    if rec.get("isMeta"):
        return "meta"
    for prefix, kind in TURN_PREFIXES:
        if head.startswith(prefix):
            return kind
    turn_origin = rec.get("turnOrigin")
    if turn_origin in ("sdk", "scheduled"):
        return str(turn_origin)
    if str(rec.get("entrypoint", "")).startswith("sdk"):
        return "sdk"
    origin = rec.get("origin")
    named = origin.get("kind") if isinstance(origin, dict) else turn_origin
    if isinstance(named, str):
        named = named.replace("-", "_")
        return named if named in ORIGIN_KINDS else "system"
    return "human"


def decision(rec: dict[str, Any], text: str) -> str | None:
    if HOOK_ERROR.match(text):
        gate = GATE_ID.search(text)
        return f"hook:{gate.group(1)}" if gate else "hook"
    pd = rec.get("permissionDecision")
    if isinstance(pd, dict):
        source = pd.get("source")
        if pd.get("decision") == "reject":
            return f"reject:{source}"
        if pd.get("decision") == "accept" and str(source).startswith("user"):
            return "approved:user"
    if text.startswith(USER_REFUSAL):
        return "reject:user"
    return None


def _h(x: Any) -> str:
    return hashlib.sha256(str(x).encode("utf-8")).hexdigest()[:16]


def fingerprint(name: str, inp: Any) -> str | None:
    """Hashes of what a mutating or shell call carried, so reverts and repeated
    commands can be found without storing text."""
    if not isinstance(inp, dict):
        return None
    if name == "Edit":
        return f"{_h(inp.get('old_string'))}>{_h(inp.get('new_string'))}"
    if name == "MultiEdit":
        edits = inp.get("edits") or []
        old = [e.get("old_string") for e in edits if isinstance(e, dict)]
        new = [e.get("new_string") for e in edits if isinstance(e, dict)]
        return f"{_h(old)}>{_h(new)}"
    if name == "Write":
        return _h(inp.get("content"))
    if name in ("Bash", "PowerShell"):
        return _h(" ".join(str(inp.get("command", "")).split()))
    return None


def tool_target(name: str, inp: Any) -> str | None:
    if not isinstance(inp, dict):
        return None
    for key in TARGET_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value:
            return value[:TARGET_MAX]
    return None


@dataclass
class Request:
    request_id: str
    seq: int
    model: str
    ts_first: str
    ts_last: str
    effort: str | None = None
    version: str | None = None
    speed: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

    def take_max(self, key: str, value: Any) -> None:
        if isinstance(value, int):
            self.usage[key] = max(self.usage.get(key, 0), value)


@dataclass
class Thread:
    thread_id: str
    session_id: str
    project: str
    path: Path
    requests: dict[str, Request] = field(default_factory=dict)
    last_seq: int = -1
    last_ts: str = ""
    tools: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    seen_tool_results: set[str] = field(default_factory=set)
    last_shape: str | None = None  # a change, even back to an earlier shape, counts


@dataclass
class Ingest:
    conn: sqlite3.Connection
    root: Path
    cost_states: dict[tuple[str, int], tuple[dict[str, Any], str, str]] = field(
        default_factory=dict
    )
    shapes_by_agent: dict[str, set[str]] = field(default_factory=dict)
    agent_threads: set[str] = field(default_factory=set)
    # Read only records before this time (ISO text; a date means its start).
    until: str | None = None
    health: Counter[tuple[str, str]] = field(default_factory=Counter)

    def run(self) -> None:
        projects = (
            p for p in self.root.iterdir() if p.is_dir() and p.name != otel.OTEL_DIR
        )
        for project in sorted(projects):
            for session_file in sorted(project.glob("*.jsonl")):
                session_id = session_file.stem
                main = Thread(session_id, session_id, project.name, session_file)
                self._thread_row(main, "main", {})
                self._read(main, is_main=True)
                for sub in sorted(
                    (project / session_id / "subagents").glob("agent-*.jsonl")
                ):
                    meta_file = sub.with_suffix(".meta.json")
                    meta = (
                        json.loads(meta_file.read_text()) if meta_file.exists() else {}
                    )
                    thread_id = sub.stem
                    if thread_id in self.agent_threads:
                        thread_id = f"{sub.stem}@{session_id}"
                    self.agent_threads.add(thread_id)
                    thread = Thread(thread_id, session_id, project.name, sub)
                    self._thread_row(thread, "subagent", meta)
                    self._read(thread, is_main=False)
        self._write_cost_states()
        self.conn.executemany(
            "INSERT INTO health VALUES (?,?,?)",
            sorted((what, name, n) for (what, name), n in self.health.items()),
        )
        otel.load(self.conn, self.root)
        self.conn.commit()

    def _thread_row(self, t: Thread, kind: str, meta: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO threads VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                t.thread_id,
                t.session_id,
                t.project,
                kind,
                meta.get("agentType"),
                meta.get("description"),
                meta.get("spawnDepth"),
                meta.get("toolUseId"),
                t.path.relative_to(self.root).as_posix(),
                meta.get("parentAgentId"),
                meta.get("requestShape"),
            ),
        )

    def _read(self, t: Thread, *, is_main: bool) -> None:
        cut = False  # a broken line counts only when another follows it
        with t.path.open("rb") as fh:
            for line in fh:
                if cut:
                    self.health[("broken", "")] += 1
                    cut = False
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    cut = True  # the last line may be cut off mid-write
                    continue
                if not isinstance(rec, dict):
                    continue
                ts = rec.get("timestamp")
                if self.until and isinstance(ts, str) and ts > self.until:
                    break  # lines are in time order
                self.health[("records", "")] += 1
                if isinstance(ts, str) and ts > t.last_ts:
                    t.last_ts = ts
                kind = rec.get("type")
                if kind == "assistant":
                    self._assistant(t, rec)
                elif kind == "user":
                    self._user(t, rec)
                elif kind == "attachment" and "rendered" in rec:
                    name = str(rec["attachment"].get("type"))
                    self._insert_prefix(
                        t.thread_id,
                        t.last_seq,
                        "attachment",
                        name,
                        text_len(rec["rendered"]),
                        None,
                    )
                    for part in parts(rec["attachment"]):
                        self.conn.execute(
                            "INSERT INTO parts VALUES (?,?,?,?,?,?)",
                            (t.thread_id, t.last_seq, *part, rec.get("timestamp")),
                        )
                    for pair in agent_tools(rec["attachment"]):
                        self.conn.execute(
                            "INSERT INTO agent_tools VALUES (?,?,?)",
                            (t.thread_id, *pair),
                        )
                elif kind == "api-request-shape":
                    self._shape(t, rec)
                elif kind == "system" and rec.get("subtype") in EVENT_SUBTYPES:
                    meta = rec.get("compactMetadata") or {}
                    self.conn.execute(
                        "INSERT INTO events VALUES (?,?,?,?,?,?,?)",
                        (
                            t.thread_id,
                            t.session_id,
                            str(ts),
                            rec["subtype"],
                            meta.get("trigger"),
                            meta.get("preTokens"),
                            meta.get("postTokens"),
                        ),
                    )
                elif kind == "cost-state" and is_main:
                    key = (str(rec["sessionId"]), int(rec["startTime"]))
                    self.cost_states[key] = (rec, t.last_ts, t.project)
                elif kind not in KNOWN_TYPES:
                    self.health[("unknown", str(kind))] += 1
        self._write_requests(t)

    def _assistant(self, t: Thread, rec: dict[str, Any]) -> None:
        msg = rec.get("message") or {}
        rid = rec.get("requestId")
        model = msg.get("model")
        if (
            not isinstance(rid, str)
            or not isinstance(model, str)
            or model == "<synthetic>"
        ):
            return
        ts = str(rec.get("timestamp", ""))
        req = t.requests.get(rid)
        if req is None:
            t.last_seq += 1
            req = Request(rid, t.last_seq, model, ts, ts)
            t.requests[rid] = req
        req.ts_first = min(req.ts_first, ts)
        req.ts_last = max(req.ts_last, ts)
        req.effort = req.effort or rec.get("effort")
        req.version = req.version or rec.get("version")
        usage = msg.get("usage") or {}
        req.speed = req.speed or usage.get("speed")
        for key in (
            "input_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
            "output_tokens",
        ):
            req.take_max(key, usage.get(key))
        cc = usage.get("cache_creation") or {}
        req.take_max("w5m", cc.get("ephemeral_5m_input_tokens"))
        req.take_max("w1h", cc.get("ephemeral_1h_input_tokens"))
        req.take_max(
            "thinking",
            (usage.get("output_tokens_details") or {}).get("thinking_tokens"),
        )
        req.take_max(
            "web", (usage.get("server_tool_use") or {}).get("web_search_requests")
        )
        for block in msg.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                tid = str(block.get("id"))
                if tid in t.tools:
                    continue
                name = str(block.get("name"))
                target = tool_target(name, block.get("input"))
                t.tools[tid] = (name, target)
                inp = block.get("input")
                chars = len(json.dumps(inp, ensure_ascii=False))
                prompt = inp.get("prompt") if isinstance(inp, dict) else None
                brief = (
                    len(prompt) if name == "Agent" and isinstance(prompt, str) else None
                )
                background = (
                    int(bool(inp.get("run_in_background")))
                    if name == "Agent" and isinstance(inp, dict)
                    else None
                )
                self.conn.execute(
                    "INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        t.thread_id,
                        req.seq,
                        "tool_use",
                        tid,
                        name,
                        target,
                        chars,
                        0,
                        ts,
                        None,
                        brief,
                        fingerprint(name, inp),
                        background,
                        None,
                    ),
                )

    def _user(self, t: Thread, rec: dict[str, Any]) -> None:
        content = (rec.get("message") or {}).get("content")
        ts = str(rec.get("timestamp", ""))
        texts = (
            [content]
            if isinstance(content, str)
            else [
                b.get("text")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            if isinstance(content, list)
            else []
        )
        texts = [x for x in texts if isinstance(x, str) and x]
        if texts:
            text = "\n".join(texts)
            kind = turn_kind(rec, text)
            self.conn.execute(
                "INSERT INTO turns VALUES (?,?,?,?,?,?,?)",
                (
                    t.thread_id,
                    t.last_seq,
                    ts,
                    kind,
                    len(text),
                    int(kind == "human" and is_correction(text)),
                    turn_source(rec),
                ),
            )
            slash = SLASH_COMMAND.search(text)
            if slash:
                self.conn.execute(
                    "INSERT INTO parts VALUES (?,?,?,?,?,?)",
                    (
                        t.thread_id,
                        t.last_seq,
                        "command",
                        slash.group(1),
                        len(text),
                        ts,
                    ),
                )
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tid = str(block.get("tool_use_id"))
            if tid in t.seen_tool_results:
                continue
            t.seen_tool_results.add(tid)
            name, target = t.tools.get(tid, (None, None))
            self.conn.execute(
                "INSERT INTO content VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.thread_id,
                    t.last_seq,
                    "tool_result",
                    tid,
                    name,
                    target,
                    text_len(block.get("content")),
                    int(bool(block.get("is_error"))),
                    ts,
                    decision(rec, plain(block.get("content"))),
                    None,
                    None,
                    None,
                    error_kind(plain(block.get("content")))
                    if block.get("is_error")
                    else None,
                ),
            )

    def _shape(self, t: Thread, rec: dict[str, Any]) -> None:
        shape_hash = str(rec.get("shapeHash"))
        agent_id = rec.get("agentId")
        # A shape that names an agent describes that agent's requests even when
        # written to the main file; its position in the agent's own timeline is
        # unknown, so its request_seq is -1.
        if isinstance(agent_id, str) and agent_id:
            thread_id, seq = f"agent-{agent_id}", -1
            seen = self.shapes_by_agent.setdefault(thread_id, set())
            if shape_hash in seen:
                return
            seen.add(shape_hash)
        else:
            # A thread's own shapes are recorded on every change, so a return to
            # an earlier toolset (h1, h2, h1) still marks where h2 stopped.
            thread_id, seq = t.thread_id, t.last_seq + 1
            if shape_hash == t.last_shape:
                return
            t.last_shape = shape_hash
        shape = rec.get("shape") or {}
        for i, block in enumerate(shape.get("system") or []):
            self._insert_prefix(
                thread_id, seq, "system", f"system[{i}]", text_len(block), shape_hash
            )
        for tool in shape.get("tools") or []:
            name = str(tool.get("name")) if isinstance(tool, dict) else "?"
            self._insert_prefix(
                thread_id,
                seq,
                "tool",
                name,
                len(json.dumps(tool, ensure_ascii=False)),
                shape_hash,
            )

    def _insert_prefix(
        self,
        thread_id: str,
        seq: int,
        source: str,
        name: str,
        chars: int,
        shape_hash: str | None,
    ) -> None:
        self.conn.execute(
            "INSERT INTO prefix VALUES (?,?,?,?,?,?)",
            (thread_id, seq, source, name, chars, shape_hash),
        )

    def _write_requests(self, t: Thread) -> None:
        for req in t.requests.values():
            u = req.usage
            if not any(u.values()):
                self.health[("no_usage", "")] += 1  # no record carried usage
            cw = u.get("cache_creation_input_tokens", 0)
            w5, w1 = u.get("w5m", 0), u.get("w1h", 0)
            unsplit = max(0, cw - w5 - w1)
            ttl = (
                "mixed"
                if w5 and w1
                else "5m"
                if w5
                else "1h"
                if w1
                else "unsplit"
                if unsplit
                else "none"
            )
            cost = request_cost(
                req.model,
                input_tokens=u.get("input_tokens", 0),
                output_tokens=u.get("output_tokens", 0),
                cache_read=u.get("cache_read_input_tokens", 0),
                cache_write_5m=w5,
                cache_write_1h=w1,
                cache_write_unsplit=unsplit,
                web_searches=u.get("web", 0),
                fast=req.speed == "fast",
            )
            self.conn.execute(
                "INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    t.thread_id,
                    req.request_id,
                    req.seq,
                    t.session_id,
                    req.model,
                    req.effort,
                    req.version,
                    req.speed,
                    req.ts_first,
                    req.ts_last,
                    u.get("input_tokens", 0),
                    u.get("cache_read_input_tokens", 0),
                    cw,
                    w5,
                    w1,
                    u.get("output_tokens", 0),
                    u.get("thinking", 0),
                    u.get("web", 0),
                    ttl,
                    cost,
                ),
            )

    def _write_cost_states(self) -> None:
        for (session_id, start), (rec, asof, project) in sorted(
            self.cost_states.items()
        ):
            for model, mu in sorted((rec.get("modelUsage") or {}).items()):
                self.conn.execute(
                    "INSERT INTO cost_state VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        session_id,
                        project,
                        start,
                        asof,
                        model,
                        mu["inputTokens"],
                        mu["outputTokens"],
                        mu.get("thinkingTokens"),
                        mu["cacheReadInputTokens"],
                        mu["cacheCreationInputTokens"],
                        mu.get("cacheCreationEphemeral5mInputTokens"),
                        mu.get("cacheCreationEphemeral1hInputTokens"),
                        mu.get("webSearchRequests", 0),
                        mu["costUSD"],
                    ),
                )


def ingest(root: Path, db: Path, until: str | None = None) -> str:
    conn = ledger.create(db)
    try:
        Ingest(conn, root, until=until).run()
        return ledger.digest(conn)
    finally:
        conn.close()
