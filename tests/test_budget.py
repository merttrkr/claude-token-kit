from pathlib import Path

from tokenlab import budget
from tokenlab.probe import Row

ROW = Row(
    label="budget",
    model="haiku",
    args=[],
    session_id="s",
    version="2.1.319",
    shape_hash="h",
    prompt=52_000,
    output=4,
    cost_usd=0.0005,
    parts={
        "attachment:skill_listing": 30_000,
        "tool:PowerShell": 9_121,
        "tool:Read": 2_006,
    },
)

CEILINGS = {
    "prompt_tokens": 52_091,
    "max_tool_chars": 9_121,
    "attachment:skill_listing": 30_060,
    "attachment:agent_listing_delta": 32_845,
}


def test_within_every_ceiling_is_green() -> None:
    assert budget.check(ROW, CEILINGS) == []


def test_creep_names_the_part_and_both_numbers() -> None:
    grown = Row(
        **{
            **ROW.__dict__,
            "prompt": 53_000,
            "parts": {**ROW.parts, "tool:Workflow": 9_500},
        }
    )
    assert budget.check(grown, CEILINGS) == [
        "prompt_tokens 53,000 > 52,091",
        "max_tool_chars 9,500 > 9,121 (tool:Workflow)",
    ]


def test_ceilings_load_by_section(tmp_path: Path) -> None:
    f = tmp_path / "budgets.toml"
    f.write_text(
        '[main]\nprompt_tokens = 52091\n"attachment:x" = 10\n'
        "[root]\nprompt_tokens = 59669\n"
    )
    assert budget.load(f) == {
        "main": {"prompt_tokens": 52_091, "attachment:x": 10},
        "root": {"prompt_tokens": 59_669},
    }


def test_a_section_is_a_probe_place() -> None:
    assert budget.SECTIONS["main"].persist
    assert not budget.SECTIONS["root"].persist
