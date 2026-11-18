from pathlib import Path

import pytest

from tokenlab import agents, rules


def test_defaults_name_no_site() -> None:
    # Without a rules file a design folder is just files, and an MCP server is itself.
    assert agents.area("/r/specs/a.md") == "file"
    assert agents.externals([("mcp__pg__query", None)]) == {"pg"}


def test_a_rules_file_overrides_numbers_areas_and_systems(tmp_path: Path) -> None:
    f = tmp_path / "rules.toml"
    f.write_text(
        '[agents]\nmin_recur = 7\n\n[[areas]]\nname = "specs"\npath = "/Specs/"\n\n'
        '[external]\npg = "db"\n',
        encoding="utf-8",
    )
    r = rules.load(f)
    assert r.min_recur == 7 and r.min_sessions == rules.DEFAULT.min_sessions
    assert agents.area("/r/specs/a.md", r) == "specs"
    assert agents.externals([("mcp__pg__query", None)], r) == {"db"}


def test_an_unknown_key_is_an_error(tmp_path: Path) -> None:
    f = tmp_path / "rules.toml"
    f.write_text("[agents]\nmin_recurr = 7\n", encoding="utf-8")
    with pytest.raises(ValueError, match="agents.min_recurr"):
        rules.load(f)
