"""Counterfactual simulator v1: replay each thread's recorded timeline under another
cache policy and re-price it.

Cache model per thread: a request reads what the previous request left cached if it
starts within the TTL of that request's start (the cache is refreshed at prefill, so a
long generation eats into a 5m TTL), else only the shared prefix, taken as the least
the thread ever read; everything else in the prompt but the uncached input is
written. The identity policy must reproduce the recorded cost per request (fidelity
gate) before any other policy's prediction counts. Hidden requests are kept as
recorded.
"""

import sqlite3
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

from tokenlab.prices import PER_TOKEN, effective_rates
from tokenlab.report import ensure_era

FIDELITY_GATE = 0.98
MAIN_TTL = 3600
SUB_TTL = 300


@dataclass(frozen=True)
class Req:
    model: str
    fast: bool
    start: float  # epoch seconds
    end: float
    inp: int
    rd: int
    w5: int
    w1: int
    out: int


@dataclass(frozen=True)
class Sim:
    rd: int
    wr: int
    cost: float


@dataclass(frozen=True)
class Policy:
    name: str
    main_ttl: int | None = None  # None keeps the thread's recorded TTL
    sub_ttl: int | None = None
    sub_model: str | None = None  # re-price Opus subagent threads on this model
    main_trim: int = 0  # tokens removed from every main-thread prompt (probe sizes)
    kind: str = "exact"


POLICIES = (
    Policy("identity"),
    Policy("main thread TTL 5m", main_ttl=300),
    Policy("subagent TTL 1h", sub_ttl=3600),
    Policy("every thread TTL 1h", main_ttl=3600, sub_ttl=3600),
    Policy(
        "Opus subagents on Sonnet 5.5",
        sub_model="claude-sonnet-5-5",
        kind="depends on behavior",
    ),
    Policy(
        "Opus subagents on Haiku 5.5",
        sub_model="claude-haiku-5-5",
        kind="depends on behavior",
    ),
)


def _price(
    model: str, fast: bool, inp: int, rd: int, w5: int, w1: int, out: int
) -> float:
    r = effective_rates(model, inp + rd + w5 + w1, fast)
    if r is None:
        return 0.0
    return (
        inp * r.input
        + rd * r.cache_read
        + w5 * r.cache_write_5m
        + w1 * r.cache_write_1h
        + out * r.output
    ) * PER_TOKEN


def recorded_cost(r: Req) -> float:
    return _price(r.model, r.fast, r.inp, r.rd, r.w5, r.w1, r.out)


def replay(
    reqs: list[Req], ttl: int, model: str | None = None, trim: int = 0
) -> list[Sim]:
    """`trim` removes that many tokens from the front of every request's prompt."""
    out: list[Sim] = []
    cached = 0
    shared = max(min((r.rd for r in reqs), default=0) - trim, 0)
    touched = 0.0
    for i, r in enumerate(reqs):
        cacheable = max(r.rd + r.w5 + r.w1 - trim, 0)
        if i == 0:
            rd = max(r.rd - trim, 0)
        elif r.start - touched <= ttl:
            rd = min(cached, cacheable)
        else:
            rd = min(shared, cacheable)
        wr = cacheable - rd
        w5, w1 = (wr, 0) if ttl <= 300 else (0, wr)
        cost = _price(model or r.model, r.fast, r.inp, rd, w5, w1, r.out)
        out.append(Sim(rd, wr, cost))
        cached = cacheable
        touched = r.start
    return out


def fidelity(reqs: Iterable[Req], sims: Iterable[Sim]) -> float:
    """1 - sum |simulated - recorded| / sum recorded, request by request."""
    err = total = 0.0
    for r, s in zip(reqs, sims, strict=True):
        rec = recorded_cost(r)
        err += abs(s.cost - rec)
        total += rec
    return 1 - err / total if total else 1.0


def _when(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


@dataclass(frozen=True)
class Thread:
    main: bool
    ttl: int
    reqs: list[Req]


def load(conn: sqlite3.Connection, since: str = "") -> dict[str, Thread]:
    """Transcript threads of the OTel era; `since` keeps requests after that time."""
    ensure_era(conn)
    rows: dict[str, list[tuple[bool, Req]]] = {}
    for thread, label, ts, dur, model, speed, inp, rd, w5, w1, out in conn.execute(
        "SELECT thread_id, label, ts, duration_ms, model, speed, input, cache_read,"
        " cache_write_5m, cache_write_1h, output FROM era"
        " WHERE thread_id IS NOT NULL AND ts > ? ORDER BY thread_id, rseq",
        (since,),
    ).fetchall():
        end = _when(ts)
        r = Req(model, speed == "fast", end - dur / 1000, end, inp, rd, w5, w1, out)
        rows.setdefault(thread, []).append((label == "main", r))
    threads: dict[str, Thread] = {}
    for thread, items in rows.items():
        reqs = [r for _, r in items]
        w1 = sum(r.w1 for r in reqs)
        w5 = sum(r.w5 for r in reqs)
        ttl = MAIN_TTL if w1 > w5 else SUB_TTL
        threads[thread] = Thread(items[0][0], ttl, reqs)
    return threads


def run(threads: dict[str, Thread], p: Policy) -> list[tuple[Req, Sim]]:
    pairs: list[tuple[Req, Sim]] = []
    for t in threads.values():
        ttl = (p.main_ttl if t.main else p.sub_ttl) or t.ttl
        model = None
        if p.sub_model and not t.main and all("opus" in r.model for r in t.reqs):
            model = p.sub_model
        trim = p.main_trim if t.main else 0
        pairs.extend(zip(t.reqs, replay(t.reqs, ttl, model, trim), strict=True))
    return pairs


def predicted_fidelity(threads: dict[str, Thread]) -> tuple[float, int]:
    """Identity fidelity over the requests the model predicts: every request but a
    thread's first, which is copied from the record. Returns (fidelity, requests)."""
    reqs: list[Req] = []
    sims: list[Sim] = []
    for t in threads.values():
        reqs += t.reqs[1:]
        sims += replay(t.reqs, t.ttl)[1:]
    return fidelity(reqs, sims), len(reqs)


def render(
    conn: sqlite3.Connection, trims: Sequence[tuple[str, int]] = (), since: str = ""
) -> str:
    """`trims`: (probe label, tokens it removes) from `probe.trims`. `since` replays
    only later requests, for a check on data the model was not fitted to."""
    (n,) = conn.execute("SELECT COUNT(*) FROM otel_requests").fetchone()
    if not n:
        return "no OTel requests in the ledger\n"
    threads = load(conn, since)
    first, last = conn.execute(
        "SELECT MIN(ts), MAX(ts) FROM otel_requests WHERE ts > ?", (since,)
    ).fetchone()
    (hidden,) = conn.execute(
        "SELECT COALESCE(SUM(cost_usd), 0) FROM era WHERE thread_id IS NULL AND ts > ?",
        (since,),
    ).fetchone()
    base = run(threads, POLICIES[0])
    recorded = sum(recorded_cost(r) for r, _ in base)
    fid, predicted = predicted_fidelity(threads)
    identity = sum(s.cost for _, s in base)
    verdict = "PASS" if fid >= FIDELITY_GATE else "FAIL: predictions do not count"
    lines = [
        f"tokenlab simulate v1: OTel era {first} .. {last}",
        f"transcript threads {len(threads)}  requests {len(base):,}"
        f"  recorded USD {recorded:,.4f} (catalog)",
        f"hidden requests USD {hidden:,.4f}, kept as recorded",
        f"identity fidelity on {predicted:,} predicted requests {fid:.4f}"
        f" (gate {FIDELITY_GATE}): {verdict}",
        "",
        f"  {'policy':34} {'simulated USD':>14} {'vs identity':>12} {'':>7}  kind",
    ]
    policies = list(POLICIES[1:]) + [
        Policy(f"main prefix -{n:,} ({label})", main_trim=n, kind="exact, probe size")
        for label, n in trims
    ]
    results = [(p, sum(s.cost for _, s in run(threads, p))) for p in policies]
    results.sort(key=lambda x: (x[1], x[0].name))
    lines.append(f"  {'identity':34} {identity:>14,.4f} {0:>12,.4f} {'':>7}  exact")
    for p, cost in results:
        delta = cost - identity
        share = f"{100 * delta / identity:+6.1f}%" if identity else "     -"
        lines.append(
            f"  {p.name[:34]:34} {cost:>14,.4f} {delta:>+12,.4f} {share:>7}  {p.kind}"
        )
    return "\n".join(lines) + "\n"
