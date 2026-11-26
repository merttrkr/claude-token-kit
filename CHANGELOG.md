# Changelog

All notable changes to this project. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

What a second user's review found ([decision 004](docs/decisions/004-second-user-review.md)):

- Windows: a file loaded once as `C:\…\CLAUDE.md` and once as `C:/…/CLAUDE.md` is one piece, not two; its findings and inventory rows no longer show twice.
- A `MEMORY.md` link, or a path in a plugin skill, may be relative to a loaded project (the folder of a loaded `CLAUDE.md`); it is no longer reported as missing.
- A memory file that reached the context as an attachment counts as read.
- Inventory: a piece whose file is gone from disk is marked `no longer on disk` and left out of the top list.
- An unused agent that is the main session's only way to a tool (e.g. `web-fetch` without `WebFetch`) is an observation, not a hide suggestion.
- Inventory: recurring tool errors show their kinds (`exit code 1`, `file not found`, …) from a fixed list; the message itself is never stored.
- File review: asks first whether to apply one change at a time or all at once with everything shown at the end; says its cost up front and offers reading in the session or with parallel readers; proposes `claudeMdExcludes` for files a hook or plugin rewrites.

## 0.2.6 — 2026-11-19

What a real user's review found the report did not show:

- **Long sessions**, a new angle: how many sessions grew past 200 thousand tokens per request, the largest request, and the share of cost above that size. An observation like the cache: shown in the Summary, not counted as cuttable.
- The Agents line says how much of all cost subagents carry and how much of their first request comes from cache.
- An agent or skill listed twice under the same name (yours and a plugin's) is reported as a copy, not as two unused items.
- MCP servers offering the same tools are shown as an observation, not counted as cuttable: one service connected twice looks the same as two environments of it.
- Inventory: MCP tools your files name that are not loaded, and `MEMORY.md` lines that grew from a pointer into a record.
- Corrections: a wider Turkish and English set (e.g. "why did you delete it", "I told you"), measured on real messages: 17 found before, 41 now.

## 0.2.5 — 2026-11-19

For a first-time, non-technical user:

- README: what it is in one sentence; install as five numbered steps (which window to open, what to paste, close and reopen Claude Code); safety in plain words; uninstall says where to type and what stays.
- Installers: every message says what to do and where; long steps say how long; a failed `claude` command shows its own message; the end says to close Claude Code completely and open it again.
- The report says when it has under a week of history ("the numbers are rough"), names the days its findings really cover, shows the token-lab version in its first line, and ends its First step with the file review (`/token-lab:optimize`). With no history it prints only that.
- The report skill tells the user about the wait and the permission prompt before running, keeps the first view to the top three angles in everyday words, and goes on to the file review. No more "it spends tokens" warnings; the file review is described as it works (the most expensive files first).

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
