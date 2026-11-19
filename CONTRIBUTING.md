# Contributing

## Setup and checks

```
uv sync
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

`make` (or `make check`) runs lint, type and test. `make fmt` formats. `make budget` is a live probe that spends a little quota; it is for the maintainer.

CI runs the same checks on Linux, macOS and Windows, and parses the installers.

## Map of the code

`src/tokenlab/`:

- `why.py` — the token map: where every prompt token goes, and which should not be there. Read-only, deterministic.
- `inventory.py` — inventory for `/token-lab:optimize`: loaded pieces with cost and use, and checks a script can make for sure.
- `rules.py` — every report threshold and site-specific mapping; defaults are generic, a TOML file overrides them.
- `ingest.py` — transcripts to ledger; rebuilds the database from scratch.
- `ledger.py` — the SQLite schema.
- `prices.py` — per-model prices copied from Claude Code's model catalog.
- `cli.py` — the command line.
- `agents.py` — agent placement report against an agent catalog.
- `episodes.py` — episode table: a prompt turn and the requests and agents it owns.
- `reconcile.py` — ledger vs Claude Code's own `cost-state`, with the hidden residual classified.
- `report.py` — attribution report over OTel `api_request` events.
- `otel.py`, `otel_sink.py` — OTel events into the ledger; a local OTLP/HTTP-JSON receiver.
- `simulate.py` — replay timelines under another cache policy and re-price.
- `probe.py`, `budget.py` — static probes with one trivial `claude -p` request; token-budget gate.
- `snapshot.py` — freeze transcripts and OTel exports into a new corpus.

Skills (`skills/`):

- `token-lab/SKILL.md` — runs the report. References in `skills/token-lab/references/`: `agent.md`, `memory.md`, `placement.md`.
- `optimize/SKILL.md` — reviews files line by line and changes them only after a yes.

Installers: `install.ps1` (Windows) and `install.sh` (macOS, Linux). Both embed the same Python block that turns on auto-update; `tests/test_release.py` checks they match.

Supported user commands are `why` and `inventory`. The other commands (ingest, reconcile, report, agents, episodes, simulate, probe, budget, otel-sink, snapshot, ...) are research tools used by a private lab. They stay as they are, but are not supported for users.

`tokenlab ingest` writes a permanent ledger. It holds file paths, shell commands and URLs from tool calls (up to 500 characters). Treat it like the transcripts. `why` and `inventory` use a temporary ledger that is deleted after the run.

## Key decisions

- The report is read-only and makes no network calls.
- Cost is "input-equivalent tokens": cache read 0.1, cache write 1.25 (5m) or 2 (1h).
- Carry model: an attachment rides until compaction; tools ride until the next request shape.
- Agent standard thresholds live in `src/tokenlab/rules.py`. A TOML file passed with `--rules` overrides them; the skills pass `~/.claude/token-lab.toml` when it exists.
- Output is English; Claude translates it for the user.
- Output is deterministic: same input, same bytes.

## Release runbook

1. Bump the version. `tests/test_release.py` checks every place (pyproject, plugin.json, marketplace.json, uv.lock, README install URLs).
2. Update `CHANGELOG.md`.
3. Point the README install URLs to the new tag.
4. `uv lock`.
5. Run the checks above.
6. Commit.
7. `git tag -a vX.Y.Z -m "vX.Y.Z"` (annotated; add `-s` to sign if a key is set up).
8. Push main and the tag.
9. Create a GitHub Release from the changelog entry.

Users with auto-update get the release within minutes of a session start. Others rerun the installer.

## Privacy rule

No personal or company data, paths or transcript text in code, tests or issues.
