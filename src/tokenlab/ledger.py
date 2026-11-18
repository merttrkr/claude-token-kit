import hashlib
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE threads (
    -- session id for the main thread, agent-<id> for a subagent
    thread_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    kind TEXT NOT NULL,              -- main | subagent
    agent_type TEXT,
    description TEXT,
    spawn_depth INTEGER,
    tool_use_id TEXT,
    file TEXT NOT NULL,
    parent_agent_id TEXT,            -- meta parentAgentId: the spawning subagent
    request_shape TEXT               -- meta requestShape: the agent's config at spawn
);
CREATE TABLE requests (
    thread_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    seq INTEGER NOT NULL,            -- order of first appearance in the thread
    session_id TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT,
    version TEXT,
    speed TEXT,
    ts_first TEXT NOT NULL,
    ts_last TEXT NOT NULL,
    input INTEGER NOT NULL,
    cache_read INTEGER NOT NULL,
    cache_write INTEGER NOT NULL,
    cache_write_5m INTEGER NOT NULL,
    cache_write_1h INTEGER NOT NULL,
    output INTEGER NOT NULL,         -- includes thinking
    thinking INTEGER NOT NULL,
    web_searches INTEGER NOT NULL,
    ttl_class TEXT NOT NULL,         -- 5m | 1h | mixed | none
    cost_usd REAL,                   -- NULL when the model has no catalog price
    PRIMARY KEY (thread_id, request_id)
);
CREATE TABLE content (
    thread_id TEXT NOT NULL,
    -- tool_use: issuing request; tool_result: last request before it
    request_seq INTEGER NOT NULL,
    kind TEXT NOT NULL,              -- tool_use | tool_result
    tool_use_id TEXT NOT NULL,
    tool TEXT,
    target TEXT,
    chars INTEGER NOT NULL,
    is_error INTEGER NOT NULL,
    ts TEXT,                         -- timestamp of the record that carried it
    -- tool_result only, NULL when plainly accepted: hook[:G<n>] (a hook blocked it),
    -- reject:<source> (permission denied), approved:user (the user had to approve)
    decision TEXT,
    brief INTEGER,                   -- Agent tool_use only: characters of the prompt
    -- tool_use only, hashes never text: Edit old>new, Write content, shell command
    fingerprint TEXT,
    background INTEGER               -- Agent tool_use only: run_in_background
);
CREATE TABLE turns (
    -- a user-role record that is not a tool result; its text is never stored
    thread_id TEXT NOT NULL,
    request_seq INTEGER NOT NULL,    -- last request before it
    ts TEXT NOT NULL,
    -- human | sdk | scheduled | interrupt | meta | compact_summary | peer
    -- | task_notification | command | system
    kind TEXT NOT NULL,
    chars INTEGER NOT NULL,
    correction INTEGER NOT NULL,     -- human turn that opens like a correction
    -- turnOrigin/promptSource/entrypoint as recorded, '-' when absent
    source TEXT NOT NULL
);
CREATE TABLE prefix (
    thread_id TEXT NOT NULL,
    -- shape: request seq at the time; attachment: last request before it
    request_seq INTEGER NOT NULL,
    source TEXT NOT NULL,            -- system | tool | attachment
    name TEXT NOT NULL,
    chars INTEGER NOT NULL,
    shape_hash TEXT
);
CREATE TABLE parts (
    -- one attachment split into what it carries; request_seq as in prefix
    thread_id TEXT NOT NULL,
    request_seq INTEGER NOT NULL,
    -- file (CLAUDE.md, memory) | skill (listing line) | agent (listing line)
    -- | mcp (server instructions) | deferred (tool name line) | rule (nested memory)
    -- | skill_body (invoked skill) | command (slash command the user typed)
    kind TEXT NOT NULL,
    name TEXT NOT NULL,              -- path, or skill / agent / server / tool name
    chars INTEGER NOT NULL,
    ts TEXT                          -- record time: orders it against compactions
);
CREATE TABLE events (
    thread_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    -- system subtype: compact_boundary, away_summary, ...
    subtype TEXT NOT NULL,
    trigger TEXT,
    pre_tokens INTEGER,
    post_tokens INTEGER
);
CREATE TABLE cost_state (
    session_id TEXT NOT NULL,
    project TEXT NOT NULL,
    start_time INTEGER NOT NULL,     -- epoch ms; one Claude process
    asof TEXT NOT NULL,              -- last timestamp written before this record
    model TEXT NOT NULL,
    input INTEGER NOT NULL,
    output INTEGER NOT NULL,
    thinking INTEGER,
    cache_read INTEGER NOT NULL,
    cache_write INTEGER NOT NULL,
    cache_write_5m INTEGER,
    cache_write_1h INTEGER,
    web_searches INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    PRIMARY KEY (session_id, start_time, model)
);
CREATE TABLE otel_requests (
    -- one OTel api_request event: every model request, transcript or not
    session_id TEXT NOT NULL,
    ts TEXT NOT NULL,                -- event.timestamp: when the response finished
    seq INTEGER NOT NULL,            -- event.sequence
    version TEXT,
    model TEXT,
    query_source TEXT,               -- repl_main_thread, generate_session_title, ...
    effort TEXT,
    speed TEXT,
    request_id TEXT,
    prompt_id TEXT,
    input INTEGER NOT NULL,
    output INTEGER NOT NULL,
    cache_read INTEGER NOT NULL,
    cache_write INTEGER NOT NULL,
    cache_write_5m INTEGER NOT NULL,
    cache_write_1h INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    duration_ms INTEGER NOT NULL,
    ttft_ms INTEGER NOT NULL,
    PRIMARY KEY (session_id, ts, seq)
);
"""

TABLE_ORDER = {
    "threads": "thread_id",
    "requests": "thread_id, seq",
    "content": "thread_id, request_seq, kind, tool_use_id",
    "turns": "thread_id, request_seq, ts, kind, chars, source",
    "prefix": "thread_id, request_seq, source, name, chars",
    "parts": "thread_id, request_seq, kind, name, chars, ts",
    "events": "thread_id, ts, subtype",
    "cost_state": "session_id, start_time, model",
    "otel_requests": "session_id, ts, seq",
}


# Lookups by thread, and the reconcile windows by session and request id (without
# them a corpus reconcile took 627 s, with them under 1 s). Indexes do not change
# rows, so the digest is unaffected.
INDEXES = """
CREATE INDEX turns_thread ON turns (thread_id, request_seq);
CREATE INDEX content_thread ON content (thread_id, request_seq);
CREATE INDEX events_thread ON events (thread_id, subtype, ts);
CREATE INDEX requests_thread ON requests (thread_id, seq);
CREATE INDEX parts_thread ON parts (thread_id, request_seq);
CREATE INDEX requests_session ON requests (session_id, ts_last);
CREATE INDEX otel_request ON otel_requests (request_id);
CREATE INDEX otel_session ON otel_requests (session_id, ts);
"""


def create(path: Path) -> sqlite3.Connection:
    path.unlink(missing_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executescript(INDEXES)
    return conn


def open_ledger(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(f"no ledger at {path}; run `tokenlab ingest` first")
    return sqlite3.connect(path)


def digest(conn: sqlite3.Connection) -> str:
    """sha256 over every row in a fixed order, independent of insertion order and
    file layout."""
    h = hashlib.sha256()
    for table, order in TABLE_ORDER.items():
        h.update(table.encode())
        for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):  # noqa: S608
            h.update(repr(row).encode())
    return h.hexdigest()
