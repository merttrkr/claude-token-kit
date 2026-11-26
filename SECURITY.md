# Security

## Report a vulnerability

Report it privately: open <https://github.com/merttrkr/claude-token-kit/security> and choose **Report a vulnerability** (GitHub private vulnerability reporting).

Do not open a public issue for a vulnerability.

## What is in scope

- The install scripts (`install.ps1`, `install.sh`).
- Auto-update (the change the installers make to `known_marketplaces.json`).
- The report and `tokenlab inventory`, which read your local transcripts.
- The optimize skill, which changes files after you say yes (to each change, or once to apply them all).

## What the tool promises

For the supported commands (`why`, `inventory`) and the two skills:

- They read local files only and make no network calls. The only download is uvx's first-run install.
- They never change your files (settings, CLAUDE.md, rules, memory, skills, agents) without your yes: to each diff, or once to the apply-all mode, which backs up every file first and shows every diff at the end.
- Their own writes, all of them:
  - `why`: a temporary database, deleted after the run; one line of numbers per run in `~/.claude/token-lab/history.jsonl` (`--no-history` turns it off).
  - `inventory`: `inventory.md`, `pieces.md` and `system-prompt.md` in a new temporary folder, or in the folder given with `--out` (files of those names there are replaced); it deletes its own leftover temporary folders (only those three files, older than 6 hours).
  - The installers: the plugin install through `claude`, and `autoUpdate` on the `token-kit` entry of `known_marketplaces.json`.

The research commands (`ingest`, `probe`, `otel-sink`, `snapshot` and the others; see CONTRIBUTING.md) are not covered: they write ledgers, logs and corpus copies, `probe` runs `claude`, and `otel-sink` listens on a local port.

When you run it through the skill, Claude reads the report. So the report goes wherever your Claude conversation goes.

## Supported version

The latest release. A security fix ships as a new release; users who pin an older tag must move to it.
