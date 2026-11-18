from tokenlab.cli import main


def test_cli_runs_without_a_command() -> None:
    assert main([]) == 0
