# claude-token-kit

A Claude Code plugin and Python CLI that reads a user's local transcripts and reports where tokens go. Public repo.

Before a change that alters what the tool reads, writes, prints or ships, read [PRINCIPLES.md](../PRINCIPLES.md) and check the change against it; name the principle in the commit or PR when one applies.

- Checks: `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`. CI runs them on Windows, macOS and Linux.
- Output (report, inventory, skills, installers) is English; Claude translates when it speaks. Output is deterministic: the same transcripts, files on disk and history file give the same bytes (the Trend table depends on the history).
- Supported user commands are `why` and `inventory`; the rest are research tools (see CONTRIBUTING.md).
- A new flag, file the tool writes, or user-visible change goes into README.md, README.tr.md and CHANGELOG.md in the same change; a new write also into SECURITY.md.
- Tests never touch the user's home or the network: `tests/conftest.py` points `Path.home()`, the default transcript folder and the history file at an empty temp folder, and blocks sockets.
- Never put transcript text, personal or company data, or real paths into code, tests, fixtures, commits or issues.
- Release steps are in CONTRIBUTING.md; `tests/test_release.py` checks the version in every place.
