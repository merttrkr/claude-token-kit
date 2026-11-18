import pytest

from tokenlab.prices import request_cost

# Real cost-state.modelUsage rows from the frozen corpus (tokens, then costUSD as
# Claude Code recorded it).
RECORDED = [
    # model, input, output, read, write_5m, write_1h, web, costUSD
    ("claude-opus-5-5", 9496, 686118, 51091512, 2048106, 314991, 0, 36.7391044),
    ("claude-opus-5-5", 19632, 209995, 4614721, 271528, 473067, 0, 10.3435482),
    ("claude-opus-5-5", 2, 816, 27934, 0, 15240, 0, 0.1438348),
    ("claude-sonnet-5-5", 8, 2921, 37067, 15049, 0, 0, 0.0705552),
    ("claude-haiku-5-5", 26732, 1914, 0, 0, 0, 2, 0.0236302),
    ("claude-haiku-4-5-20251001", 11003, 512, 0, 0, 0, 1, 0.023563),
]


@pytest.mark.parametrize(
    ("model", "inp", "out", "read", "w5", "w1", "web", "recorded"), RECORDED
)
def test_catalog_reproduces_recorded_cost(
    model: str,
    inp: int,
    out: int,
    read: int,
    w5: int,
    w1: int,
    web: int,
    recorded: float,
) -> None:
    cost = request_cost(
        model,
        input_tokens=inp,
        output_tokens=out,
        cache_read=read,
        cache_write_5m=w5,
        cache_write_1h=w1,
        web_searches=web,
    )
    assert cost == pytest.approx(recorded, rel=1e-9)


def test_unsplit_writes_are_priced_at_the_1h_rate() -> None:
    # e93bdae3 recorded 435,126 1h writes plus 358,789 with no split; costUSD matches
    # only at the 1h rate.
    cost = request_cost(
        "claude-opus-5-5",
        input_tokens=6873,
        output_tokens=65404,
        cache_read=9729575,
        cache_write_5m=0,
        cache_write_1h=435126,
        cache_write_unsplit=358789,
    )
    assert cost == pytest.approx(9.632807, rel=1e-9)


def test_haiku_55_long_prompt_tier_applies_above_100k() -> None:
    small = request_cost(
        "claude-haiku-5-5",
        input_tokens=100_000,
        output_tokens=0,
        cache_read=0,
        cache_write_5m=0,
        cache_write_1h=0,
    )
    large = request_cost(
        "claude-haiku-5-5",
        input_tokens=100_001,
        output_tokens=0,
        cache_read=0,
        cache_write_5m=0,
        cache_write_1h=0,
    )
    assert small == pytest.approx(0.01)
    assert large == pytest.approx(100_001 * 0.5e-6)


def test_fast_mode_doubles_opus_55() -> None:
    kw = {
        "input_tokens": 1_000_000,
        "output_tokens": 0,
        "cache_read": 0,
        "cache_write_5m": 0,
        "cache_write_1h": 0,
    }
    assert request_cost("claude-opus-5-5", fast=True, **kw) == pytest.approx(8.0)


def test_unknown_model_has_no_price() -> None:
    assert (
        request_cost(
            "claude-unknown",
            input_tokens=1,
            output_tokens=1,
            cache_read=0,
            cache_write_5m=0,
            cache_write_1h=0,
        )
        is None
    )
