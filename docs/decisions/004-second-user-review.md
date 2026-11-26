# 004 — A second user's review

Date: 2026-11-26 · Status: accepted

## Problem

A second user ran the report and the file review end to end on Windows. What went wrong or was missing:

- The same file counted twice (`C:\…\CLAUDE.md` and `C:/…/CLAUDE.md`): every finding about it showed twice.
- `MEMORY.md` links written relative to the project, and repo paths in plugin skills, were reported as missing.
- "Memory file never read" counted only the Read tool; files that reached the context as attachments looked unread.
- Deleted files (a removed worktree's rules) ranked among the most expensive pieces to review.
- `web-fetch` was proposed for hiding as unused, though it was the main session's only way to `WebFetch`.
- Files a hook rewrites (`AUTO-GENERATED`) were proposed for edits that do not last; `claudeMdExcludes` was the fix that worked.
- The tool error table had no hint of what the errors were.
- The file review asked a yes per change (too many questions) and did not say its cost (about 830 thousand tokens with 4 parallel readers).

## Evidence

- The user's review (numbers from their machine).
- On the maintainer's transcripts: `file` attachments carry memory files into the context; of 2,115 agent listing lines, 1,630 end in a tool list, 194 in `(Tools: *)`, 194 in "All tools except", 97 have none; 472 tool errors sorted by a fixed list of kinds leave 13% as "other".
- Claude Code docs (memory page): `claudeMdExcludes` takes glob patterns matched against absolute paths, at any settings layer, and cannot exclude a managed CLAUDE.md.

## Options

For the error text:

1. Keep the table as is; the model reads the transcript places. Safest, costs the model's reading.
2. Store a masked first line of the error. Against: `tokenlab ingest` can write a lasting ledger, and masking is never complete; an error can hold a secret or a colleague's words.
3. Store only a kind from a fixed list (`exit code N`, `file not found`, `timeout`, …), never the text.

For the questions in the file review: a per-change yes only (today), or a mode chosen once at the start that applies everything and shows every diff at the end.

## Decision

- Error text: option 3 (principle 1: read the least, copy no text). `ERROR_KINDS` in `ingest.py`; unmatched errors are `other`.
- Apply-all mode: asked first in `/token-lab:optimize`, default one at a time. Choosing it is the yes; every file is backed up first and every diff is shown at the end. Principle 1 and "What we do not do" now say so.
- The rest as fixes: Windows paths normalised at ingest; relative links also looked up from the folder of each loaded CLAUDE.md; attachments count as reads (not as cost); gone files marked and left out of the top list; an unused agent that is a thread's only way to a tool is an observation, not cuttable; `claudeMdExcludes` in the placement table; the cost range said up front, with a choice of reading in the session or with parallel readers.
- This release carries several user-facing topics, against principle 5. A one-time exception: the project is at its start and the changes answer one review.

## How to undo

Each fix is its own block in `ingest.py`, `why.py` or `inventory.py`; the two skill changes are text in `skills/optimize/SKILL.md` and `placement.md`. The new `agent_tools` table and the `error_kind` column can be dropped with their readers.
