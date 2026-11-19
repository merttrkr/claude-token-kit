# 002 — Built for a first-time, non-technical user

Date: 2026-11-19 · Status: accepted

## Problem

A user on 0.2.4 ran the report and never found the file review (`/token-lab:optimize`). A walk-through as a first-time, non-technical user found more places to get stuck: which window to type the install command into, a running Claude Code not seeing a newly installed `uv`, a silent minute-long wait and a permission prompt nobody announced, a jargon-heavy first view, a one-day history reported as "never used in 30 days", and warnings that the review "spends tokens".

## Evidence

- The user's report: no pointer to the file review; the offer came only after every report item.
- Fresh Linux container runs (no Claude Code, no uv, no history, one day of history): the messages each case printed.
- An independent read of README, skills and installer messages as a non-technical user.

## Options

1. Keep the flow; document it better in the README. Against: the user never reads the README again after installing.
2. Change the flow: the report leads into the file review; the report itself names it; every message says what to do and where. For: the user is led, not told.

## Decision

Option 2 (principles 1 and 2). The report ends its First step with a fixed "Next: /token-lab:optimize" line; the report skill goes on to the file review after a short first view; no token-cost warnings (any skill spends tokens; the estimate is given when asked); a history under 7 days is called rough; installers say where to act and to restart Claude Code completely.

## How to undo

Revert the skill text and the "Next" line in `why.py`; the rest is wording.
