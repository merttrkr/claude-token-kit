"""Per-model prices, copied from the model catalog baked into Claude Code 2.1.314.

These reproduce `cost-state.modelUsage[*].costUSD` exactly wherever the record
carries the 5m/1h cache-write split, so they replace the fitted rates the plan
first proposed. Opus 5.5 and Sonnet 5.5 price cache reads at 0.05x input, not 0.1x.
"""

from dataclasses import dataclass

PER_TOKEN = 1e-6


@dataclass(frozen=True)
class Rates:
    input: float
    output: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    web_search: float = 0.01
    long_prompt_above: int | None = None
    long_prompt: "Rates | None" = None


_HAIKU_55_LONG = Rates(0.5, 2.5, 0.625, 1.0, 0.05)

# USD per million tokens; web_search is USD per request.
CATALOG: dict[str, Rates] = {
    "claude-opus-5-5": Rates(4, 20, 5, 8, 0.2),
    "claude-opus-5": Rates(5, 25, 6.25, 10, 0.5),
    "claude-sonnet-5-5": Rates(2, 10, 2.5, 4, 0.1),
    "claude-sonnet-5": Rates(2, 10, 2.5, 4, 0.2),
    "claude-sonnet-4-6": Rates(3, 15, 3.75, 6, 0.3),
    "claude-haiku-5-5": Rates(
        0.1, 0.5, 0.125, 0.2, 0.01, 0.01, 100_000, _HAIKU_55_LONG
    ),
    "claude-haiku-4-5-20251001": Rates(1, 5, 1.25, 2, 0.1),
}

# Fast mode on first party: 2x the standard Opus 5.5 table.
FAST: dict[str, Rates] = {"claude-opus-5-5": Rates(8, 40, 10, 16, 0.4)}


def rates_for(model: str, fast: bool = False) -> Rates | None:
    if fast and model in FAST:
        return FAST[model]
    return CATALOG.get(model)


def effective_rates(model: str, prompt: int, fast: bool = False) -> Rates | None:
    """The rates one request pays: the long-prompt table above its threshold."""
    r = rates_for(model, fast)
    if r is None:
        return None
    if (
        r.long_prompt is not None
        and r.long_prompt_above is not None
        and prompt > r.long_prompt_above
    ):
        return Rates(**{**r.long_prompt.__dict__, "web_search": r.web_search})
    return r


def request_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_write_5m: int,
    cache_write_1h: int,
    cache_write_unsplit: int = 0,
    web_searches: int = 0,
    fast: bool = False,
) -> float | None:
    """USD for one request. Unsplit cache writes are priced at the 1h rate, as
    2.1.314 does."""
    prompt = (
        input_tokens
        + cache_read
        + cache_write_5m
        + cache_write_1h
        + cache_write_unsplit
    )
    r = effective_rates(model, prompt, fast)
    if r is None:
        return None
    tokens = (
        input_tokens * r.input
        + output_tokens * r.output
        + cache_read * r.cache_read
        + cache_write_5m * r.cache_write_5m
        + (cache_write_1h + cache_write_unsplit) * r.cache_write_1h
    )
    return tokens * PER_TOKEN + web_searches * r.web_search
