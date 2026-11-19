# 003 — What the report missed

Date: 2026-11-19 · Status: accepted

## Problem

Asked to look past the report, a user's Claude found five things the report did not show: sessions growing to 400 thousand tokens per request (the biggest cost, unmeasured); subagents carrying more than half of the traffic while the report said "5%" (only the cuttable part); 27 agents listed twice (own and plugin copy) reported as unused; one service connected through three MCP servers; and file contents (memory index lines turned into records, a rule naming a tool that is not loaded). Corrections were also under-counted.

## Evidence

- The user's review (numbers from their machine).
- On the maintainer's transcripts: 104 of 270 sessions grew past 200 thousand tokens per request, the largest 567 thousand, 20% of cost above that size; subagents carry 38% of cost; two copies of one browser server offer 25 tools of the same name; a skill names `claude-in-chrome` tools that are not loaded.
- Corrections, on 2,227 real messages: the opening-word set caught 17; the wider set and the phrases catch 41; a sample of the new ones were all corrections or objections. A bare "yanlış" inside a sentence was left out: it mostly described a bug.

## Options

1. Count long sessions as cuttable waste. Against: the cuttable total jumped from 9% to 30%; a new session has its own cost; it overlaps the cache and tool-output angles.
2. Show long sessions as an observation (like the cache). For: the user sees the largest lever first in the Summary, the cuttable total stays honest.

## Decision

Option 2 for long sessions (principles 1 and 3); the agents' share and copies as findings; overlapping servers as an observation too, since two environments of one service look the same to a script; the two file checks in the inventory, where the model reads them. Thresholds, with the data they came from:

- 200 thousand tokens per request (rules `long_context_tokens`): the long-context size; 104 of 270 sessions passed it.
- 3 shared tool names (rules `overlap_tools`): on the maintainer's machine, unrelated servers share at most one name; the two copies of one browser server share 25.
- 200 characters for a `MEMORY.md` list line (`LONG_INDEX_LINE` in `inventory.py`, not in the rules file): the maintainer's 50 pointer lines are 126 characters at the median and 195 at most; the records the user's review found were longer.

## How to undo

Each is its own block in `why.py` or `inventory.py`; the two rules thresholds can be raised in `~/.claude/token-lab.toml`.
