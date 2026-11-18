"""Ledger vs Claude Code's own `cost-state`, per process and model.

A `cost-state` record covers one Claude process (its `startTime`) up to the moment
it was written, including subagents and requests that never reach a transcript.
Whatever the ledger lacks is the hidden residual. It is classified by source and
reported, never dropped:

- background: a model with usage in cost-state but no request in any transcript
  (titles, summaries).
- compaction: the process compacted; the summary request is not written as an
  assistant message.
- matched: neither applies. The output gate is judged on these rows, where the ledger
  should be complete.
"""

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

FIELDS = ("input", "output", "cache_read", "cache_write")
CLASSES = ("matched", "compaction", "background")
OUTPUT_TOLERANCE = 0.02
OTEL_TOLERANCE = 0.01
# cost-state is written after the response that precedes it; allow for subagent lines
# landing just after.
ASOF_SLACK = timedelta(seconds=1)


@dataclass(frozen=True)
class Row:
    session_id: str
    project: str
    start_time: int
    model: str
    requests: int
    compactions: int
    ledger: dict[str, int]
    truth: dict[str, int]
    ledger_cost: float
    truth_cost: float

    @property
    def source(self) -> str:
        if self.requests == 0:
            return "background"
        return "compaction" if self.compactions else "matched"


def _iso(dt: datetime) -> str:
    return (
        dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.")
        + f"{dt.microsecond // 1000:03d}Z"
    )


def _window(start: int, asof: str) -> tuple[str, str]:
    lo = _iso(datetime.fromtimestamp(start / 1000, UTC))
    if not asof:
        return lo, lo
    return lo, _iso(datetime.fromisoformat(asof.replace("Z", "+00:00")) + ASOF_SLACK)


def rows(conn: sqlite3.Connection) -> list[Row]:
    out: list[Row] = []
    states = conn.execute(
        "SELECT session_id, project, start_time, asof, model, input, output,"
        " cache_read, cache_write, cost_usd"
        " FROM cost_state ORDER BY project, session_id, start_time, model"
    ).fetchall()
    for session_id, project, start, asof, model, *truth_vals, truth_cost in states:
        lo, hi = _window(start, asof)
        n, *ledger_vals, cost = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input),0), COALESCE(SUM(output),0),"
            " COALESCE(SUM(cache_read),0),"
            " COALESCE(SUM(cache_write),0), COALESCE(SUM(cost_usd),0)"
            " FROM requests WHERE session_id=? AND model=?"
            " AND ts_last>=? AND ts_last<=?",
            (session_id, model, lo, hi),
        ).fetchone()
        (compactions,) = conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id=?"
            " AND subtype='compact_boundary' AND ts>=? AND ts<=?",
            (session_id, lo, hi),
        ).fetchone()
        out.append(
            Row(
                session_id,
                project,
                start,
                model,
                n,
                compactions,
                dict(zip(FIELDS, ledger_vals, strict=True)),
                dict(zip(FIELDS, truth_vals, strict=True)),
                cost,
                truth_cost,
            )
        )
    return out


def totals(rs: list[Row]) -> tuple[dict[str, int], dict[str, int], float, float]:
    led = {f: sum(r.ledger[f] for r in rs) for f in FIELDS}
    tru = {f: sum(r.truth[f] for r in rs) for f in FIELDS}
    return led, tru, sum(r.ledger_cost for r in rs), sum(r.truth_cost for r in rs)


def ratio(a: float, b: float) -> float:
    return a / b if b else 1.0


def output_reconciles(rs: list[Row]) -> bool:
    led, tru, _, _ = totals([r for r in rs if r.source == "matched"])
    return abs(1 - ratio(led["output"], tru["output"])) <= OUTPUT_TOLERANCE


def _table(rs: list[Row]) -> list[str]:
    led, tru, lc, tc = totals(rs)
    lines = [
        f"  {'field':12} {'ledger':>14} {'cost-state':>14}"
        f" {'residual':>14} {'ratio':>7}"
    ]
    for f in FIELDS:
        lines.append(
            f"  {f:12} {led[f]:>14,} {tru[f]:>14,} {tru[f] - led[f]:>14,}"
            f" {ratio(led[f], tru[f]):>7.3f}"
        )
    lines.append(
        f"  {'cost USD':12} {lc:>14,.2f} {tc:>14,.2f} {tc - lc:>14,.2f}"
        f" {ratio(lc, tc):>7.3f}"
    )
    return lines


def render(rs: list[Row], *, per_session: bool = False) -> str:
    lines: list[str] = []
    if per_session:
        lines.append(
            f"{'session':8} {'process':>13} {'model':26} {'source':10} {'n':>5}"
            f" {'output':>7} {'read':>7} {'write':>7} {'cost':>7}"
        )
        for r in rs:
            lines.append(
                f"{r.session_id[:8]} {r.start_time:>13} {r.model:26} {r.source:10}"
                f" {r.requests:>5}"
                + "".join(
                    f" {ratio(r.ledger[f], r.truth[f]):>7.3f}"
                    for f in ("output", "cache_read", "cache_write")
                )
                + f" {ratio(r.ledger_cost, r.truth_cost):>7.3f}"
            )
        lines.append("")
    lines.append(
        f"processes reconciled: {len({(r.session_id, r.start_time) for r in rs})}"
    )
    lines.append("all rows")
    lines.extend(_table(rs))
    for source in CLASSES:
        subset = [r for r in rs if r.source == source]
        lines.append(f"{source}: {len(subset)} rows")
        lines.extend(_table(subset))
    verdict = "PASS" if output_reconciles(rs) else "FAIL"
    lines.append(f"output within {OUTPUT_TOLERANCE:.0%} on matched rows: {verdict}")
    return "\n".join(lines) + "\n"


# OTel sees every request, so on processes it covered it should equal cost-state.
# A process counts as covered when it has OTel events and every transcript request in
# its window has an OTel twin. A process the sink saw only in part (started late, went
# down) is left out of the gate and listed by `partial`; a hidden request missing from
# OTel in a covered process still shows up as a residual.


@dataclass(frozen=True)
class OtelRow:
    session_id: str
    start_time: int
    model: str
    requests: int
    otel: dict[str, int]
    truth: dict[str, int]
    otel_cost: float
    truth_cost: float


_OTEL_WINDOW = "FROM otel_requests WHERE session_id=? AND ts>=? AND ts<=?"


def _coverage(
    conn: sqlite3.Connection, session_id: str, lo: str, hi: str
) -> tuple[int, int]:
    """OTel events in the window, and transcript requests there without an OTel twin."""
    (events,) = conn.execute(
        f"SELECT COUNT(*) {_OTEL_WINDOW}",  # noqa: S608
        (session_id, lo, hi),
    ).fetchone()
    (missing,) = conn.execute(
        "SELECT COUNT(*) FROM requests r WHERE r.session_id=?"
        " AND r.ts_last>=? AND r.ts_last<=? AND NOT EXISTS"
        " (SELECT 1 FROM otel_requests o WHERE o.request_id=r.request_id)",
        (session_id, lo, hi),
    ).fetchone()
    return events, missing


def partial(conn: sqlite3.Connection) -> list[tuple[str, int, int]]:
    """Processes with OTel events but transcript requests the sink missed:
    (session_id, start_time, transcript requests without OTel)."""
    out: list[tuple[str, int, int]] = []
    for session_id, start, asof in conn.execute(
        "SELECT DISTINCT session_id, start_time, asof FROM cost_state"
        " ORDER BY project, session_id, start_time"
    ).fetchall():
        events, missing = _coverage(conn, session_id, *_window(start, asof))
        if events and missing:
            out.append((session_id, start, missing))
    return out


def otel_rows(conn: sqlite3.Connection) -> list[OtelRow]:
    out: list[OtelRow] = []
    states = conn.execute(
        "SELECT session_id, start_time, asof, model, input, output,"
        " cache_read, cache_write, cost_usd"
        " FROM cost_state ORDER BY project, session_id, start_time, model"
    ).fetchall()
    for session_id, start, asof, model, *truth_vals, truth_cost in states:
        lo, hi = _window(start, asof)
        window = _OTEL_WINDOW
        events, missing = _coverage(conn, session_id, lo, hi)
        if not events or missing:
            continue
        n, *otel_vals, cost = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(input),0), COALESCE(SUM(output),0),"
            " COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_write),0),"
            f" COALESCE(SUM(cost_usd),0) {window} AND model=?",  # noqa: S608
            (session_id, lo, hi, model),
        ).fetchone()
        out.append(
            OtelRow(
                session_id,
                start,
                model,
                n,
                dict(zip(FIELDS, otel_vals, strict=True)),
                dict(zip(FIELDS, truth_vals, strict=True)),
                cost,
                truth_cost,
            )
        )
    return out


def otel_reconciles(rs: list[OtelRow]) -> bool | None:
    """Every field and the cost within 1% in total; None when OTel covers nothing."""
    if not rs:
        return None
    pairs: list[tuple[float, float]] = [
        (sum(r.otel[f] for r in rs), sum(r.truth[f] for r in rs)) for f in FIELDS
    ]
    pairs.append((sum(r.otel_cost for r in rs), sum(r.truth_cost for r in rs)))
    return all(abs(1 - ratio(a, b)) <= OTEL_TOLERANCE for a, b in pairs)


def hidden(
    conn: sqlite3.Connection,
) -> list[tuple[str, int, int, int, int, int, float]]:
    """OTel requests with no transcript request of the same id, by query_source."""
    return conn.execute(
        "SELECT COALESCE(query_source, '?'), COUNT(*), SUM(input), SUM(output),"
        " SUM(cache_read), SUM(cache_write), SUM(cost_usd) FROM otel_requests o"
        " WHERE NOT EXISTS (SELECT 1 FROM requests r WHERE r.request_id=o.request_id)"
        " GROUP BY 1 ORDER BY 7 DESC, 1"
    ).fetchall()


def twins(conn: sqlite3.Connection) -> tuple[int, int]:
    """OTel requests also in a transcript, and how many of those carry the same
    usage there: a check on the ledger's rule for picking the final streamed line."""
    n, same = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(o.input=r.input AND o.output=r.output"
        " AND o.cache_read=r.cache_read AND o.cache_write=r.cache_write), 0)"
        " FROM otel_requests o JOIN (SELECT DISTINCT request_id, input, output,"
        " cache_read, cache_write FROM requests) r ON r.request_id=o.request_id"
    ).fetchone()
    return n, same


def render_otel(conn: sqlite3.Connection) -> str:
    (events,) = conn.execute("SELECT COUNT(*) FROM otel_requests").fetchone()
    if not events:
        return ""
    rs = otel_rows(conn)
    lines = [
        "",
        f"otel: {events} api_request events,"
        f" {len({(r.session_id, r.start_time) for r in rs})} processes covered",
        f"  {'field':12} {'otel':>14} {'cost-state':>14} {'residual':>14} {'ratio':>7}",
    ]
    for f in FIELDS:
        o, t = sum(r.otel[f] for r in rs), sum(r.truth[f] for r in rs)
        lines.append(f"  {f:12} {o:>14,} {t:>14,} {t - o:>14,} {ratio(o, t):>7.3f}")
    oc, tc = sum(r.otel_cost for r in rs), sum(r.truth_cost for r in rs)
    lines.append(
        f"  {'cost USD':12} {oc:>14,.4f} {tc:>14,.4f} {tc - oc:>14,.4f}"
        f" {ratio(oc, tc):>7.3f}"
    )
    n, same = twins(conn)
    lines.append(f"in transcripts too: {n} requests, {same} with identical usage")
    lines.append("hidden from transcripts, by query_source")
    lines.append(
        f"  {'query_source':28} {'n':>5} {'input':>10} {'output':>10}"
        f" {'cache_read':>12} {'cache_write':>12} {'cost USD':>10}"
    )
    for src, k, inp, out, rd, wr, cost in hidden(conn):
        lines.append(
            f"  {src:28} {k:>5} {inp:>10,} {out:>10,} {rd:>12,} {wr:>12,} {cost:>10.4f}"
        )
    parts = partial(conn)
    lines.append(f"partly covered, left out of the gate: {len(parts)} processes")
    for session_id, start, missing in parts:
        lines.append(
            f"  {session_id[:8]} {start}  transcript requests without OTel: {missing}"
        )
    verdict = {None: "n/a", True: "PASS", False: "FAIL"}[otel_reconciles(rs)]
    lines.append(f"otel within {OTEL_TOLERANCE:.0%} of cost-state: {verdict}")
    return "\n".join(lines) + "\n"
