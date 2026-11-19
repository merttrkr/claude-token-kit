# 001 — A history of report numbers for a trend

Date: 2026-11-19 · Status: accepted

## Problem

Users could not see whether the tool helped. Claude Code deletes transcripts after `cleanupPeriodDays` (30 days by default), so a report rebuilt later cannot show the state before a change; the report kept nothing.

## Evidence

- The maintainer's transcripts reach back 31 days; `--until` alone compares only inside that window, and reads files on disk as they are now.
- On those transcripts a report as of two weeks earlier gives a fixed part per request of 68 thousand tokens and cuttable waste of 15%, against 57 thousand and 9% now: the change shows only with an earlier point.

## Options

1. Keep nothing; document `--until`. For: no write. Against: no comparison past 30 days; no trend for a user who does not know the flag.
2. Keep each run's numbers in a small local file and show a Trend table. For: progress visible from the first or second run. Against: a write; the read-only wording needs an exception.
3. Telemetry. Rejected by principle 1.

## Decision

Option 2 (principles 1 and 2): one JSON line of numbers per run in `~/.claude/token-lab/history.jsonl`: window end and days, fixed part per request, cuttable share, each angle's share, a hash of the transcript folder and the tool version. No names, paths or text. `--no-history` turns it off.

Thresholds, chosen rather than measured:

- The first run measures an earlier point 14 days back (halfway through a shorter history, none under 2 days): inside the default 30-day transcript life, with two weeks of change to show.
- The oldest Trend column is the run closest to 28 days back: about a month.
- 400 lines kept: more than a year of daily runs, a few hundred kilobytes.

## How to undo

`--no-history`, or delete `~/.claude/token-lab/`. In code: remove the `history` and `out` arguments of `why.render` and the history functions in `cli.py`; older history files are then ignored.
