import sqlite3
from pathlib import Path

import pytest
from test_otel import S1_EVENTS, build, export

from tokenlab import simulate
from tokenlab.simulate import Req

OPUS = "claude-opus-5-5"
GOLDEN = Path(__file__).parent / "golden" / "simulate_mini.txt"


def req(start: float, end: float, inp: int, rd: int, w5: int = 0, w1: int = 0) -> Req:
    return Req(
        model=OPUS,
        fast=False,
        start=start,
        end=end,
        inp=inp,
        rd=rd,
        w5=w5,
        w1=w1,
        out=100,
    )


# A subagent (5m TTL) pauses 10 minutes: the second request misses and reads only
# the shared prefix it read on its cold start.
SUB = [req(0, 10, 5, 20_000, w5=30_000), req(610, 620, 5, 20_000, w5=40_000)]
# A main thread (1h TTL) pauses 30 minutes: the second request still hits.
MAIN = [req(0, 10, 5, 20_000, w1=30_000), req(1810, 1820, 5, 50_000, w1=10_000)]


def test_identity_reproduces_a_miss_after_the_ttl() -> None:
    sims = simulate.replay(SUB, ttl=300)
    assert [(s.rd, s.wr) for s in sims] == [(20_000, 30_000), (20_000, 40_000)]
    assert simulate.fidelity(SUB, sims) == pytest.approx(1.0)


def test_identity_reproduces_a_hit_within_the_ttl() -> None:
    sims = simulate.replay(MAIN, ttl=3600)
    assert [(s.rd, s.wr) for s in sims] == [(20_000, 30_000), (50_000, 10_000)]
    assert simulate.fidelity(MAIN, sims) == pytest.approx(1.0)


def test_a_longer_ttl_turns_the_miss_into_a_hit_at_the_1h_write_rate() -> None:
    sims = simulate.replay(SUB, ttl=3600)
    assert [(s.rd, s.wr) for s in sims] == [(20_000, 30_000), (50_000, 10_000)]
    # 1h writes cost 8/M on Opus 5.5 instead of 5/M.
    usd = (5 * 4 + 20_000 * 0.2 + 30_000 * 8 + 100 * 20) / 1e6
    assert sims[0].cost == pytest.approx(usd)


def test_a_shorter_ttl_turns_the_hit_into_a_miss() -> None:
    sims = simulate.replay(MAIN, ttl=300)
    assert [(s.rd, s.wr) for s in sims] == [(20_000, 30_000), (20_000, 40_000)]


def test_the_ttl_runs_from_the_previous_request_start() -> None:
    # The second request starts 50s after the first one ended, but 320s after it
    # started: the 5m cache, refreshed at the first one's prefill, has expired.
    reqs = [req(0, 270, 5, 20_000, w5=30_000), req(320, 330, 5, 20_000, w5=40_000)]
    assert [(s.rd, s.wr) for s in simulate.replay(reqs, ttl=300)][1] == (20_000, 40_000)


def test_a_cold_read_is_the_least_the_thread_read() -> None:
    # The era starts mid-thread with a warm 150k read; after an idle hour the thread
    # reads only the 30k shared prefix again.
    reqs = [
        req(0, 10, 5, 150_000, w1=1_000),
        req(4000, 4010, 5, 30_000, w1=125_000),
    ]
    assert [(s.rd, s.wr) for s in simulate.replay(reqs, ttl=3600)][1] == (
        30_000,
        125_000,
    )


def test_a_prefix_trim_comes_off_every_read_and_the_cold_write() -> None:
    sims = simulate.replay(SUB, ttl=300, trim=5_000)
    assert [(s.rd, s.wr) for s in sims] == [(15_000, 30_000), (15_000, 40_000)]
    hits = simulate.replay(MAIN, ttl=3600, trim=5_000)
    assert [(s.rd, s.wr) for s in hits] == [(15_000, 30_000), (45_000, 10_000)]


def test_a_trim_larger_than_the_cached_prefix_comes_off_the_write() -> None:
    reqs = [req(0, 10, 5, 1_000, w1=30_000)]
    assert [(s.rd, s.wr) for s in simulate.replay(reqs, ttl=3600, trim=5_000)] == [
        (0, 26_000)
    ]


def test_predicted_fidelity_leaves_out_each_thread_first_request() -> None:
    # Recorded as a miss 100s later: identity predicts a hit, so only the second
    # request is wrong, and the first one, copied from the record, can't hide it.
    reqs = [req(0, 10, 5, 20_000, w5=300_000), req(110, 120, 5, 20_000, w5=290_000)]
    sims = simulate.replay(reqs, ttl=300)
    rec = simulate.recorded_cost(reqs[1])
    whole = simulate.fidelity(reqs, sims)
    assert simulate.fidelity(reqs[1:], sims[1:]) == pytest.approx(
        1 - abs(sims[1].cost - rec) / rec
    )
    assert simulate.fidelity(reqs[1:], sims[1:]) < whole


def test_since_keeps_only_later_requests(conn: sqlite3.Connection) -> None:
    from conftest import ts

    threads = simulate.load(conn, since=ts(4))
    assert sum(len(t.reqs) for t in threads.values()) == 2


def test_repricing_on_another_model_keeps_the_tokens() -> None:
    sims = simulate.replay(SUB, ttl=300, model="claude-sonnet-5-5")
    assert [(s.rd, s.wr) for s in sims] == [(20_000, 30_000), (20_000, 40_000)]
    usd = (5 * 2 + 20_000 * 0.1 + 30_000 * 2.5 + 100 * 10) / 1e6
    assert sims[0].cost == pytest.approx(usd)


@pytest.fixture
def conn(mini_corpus: Path, tmp_path: Path) -> sqlite3.Connection:
    return build(mini_corpus, tmp_path, export(*S1_EVENTS))


def test_render_is_byte_identical_and_matches_golden(conn: sqlite3.Connection) -> None:
    text = simulate.render(conn)
    assert simulate.render(conn) == text
    assert text == GOLDEN.read_text(encoding="utf-8")
