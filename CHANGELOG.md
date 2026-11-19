# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 0.2.4 — 2026-11-19

- A **Trend** table at the top of the report: the fixed part per request, the cuttable waste and each angle's share of cost, against the last run and a run about a month back.
- PRINCIPLES.md: how the project is built and moved forward, each principle with its check; a repo CLAUDE.md, a PR template and a decision-note template (`docs/decisions/`).
- The numbers come from `~/.claude/token-lab/history.jsonl`, one line per run (numbers only); `--no-history` turns it off. The first run also measures two weeks back once, so the trend shows from day one.

## 0.2.3 — 2026-11-19

- README: the privacy note says plainly that Claude reads the report like any file in the session.
- Install commands are pinned to the release tag; releases are annotated tags.
- Installers check that the installed version is the latest after an update.
- `tokenlab why --until DATE`: the report as it was on a date, to compare before and after a change.
- The report says how much of Claude Code's own cost counter the transcripts cover, and warns when it meets records it cannot read.
- `tokenlab inventory` removes leftover inventory folders from earlier runs.
- CI on Windows, macOS and Linux; SECURITY.md, CONTRIBUTING.md and this changelog.

## 0.2.2

- The report, the inventory, both skills and the installers are now in English.
- The issue template is in English too (`problem-or-idea.md`).
- Numbers read as `12%` and `11.3 billion`; counts take the singular or plural form.

## 0.2.1

- The report now offers `/token-lab:optimize` at the end.
- The installers turn on auto-update for the `token-kit` marketplace, unless you turned it off yourself.
- README and skill text updated to match.

## 0.2.0

- New skill `/token-lab:optimize`: goes through what Claude reads, line by line, against the placement standard. It changes files only after your yes.
- New command `tokenlab inventory`: lists every loaded CLAUDE.md, rule, memory index, skill and agent with its cost and use, and the checks a script can make for sure.

## 0.1.2

- Agent candidates are measured against the agent standard.
- The report shows which general-purpose work could go to an existing agent.

## 0.1.1

- The report starts with a summary.
- English report text; macOS and Linux install script (`install.sh`).
- Reference notes for agents, memory and placement standards.
- Turkish README (`README.tr.md`).

## 0.1.0

- First trial release: the token map (`tokenlab why`) shows where Claude Code tokens go and which should not be there, from your own transcripts.
- Claude Code plugin with the `token-lab` skill and a one-command Windows installer.
