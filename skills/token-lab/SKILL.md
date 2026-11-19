---
name: token-lab
description: Token map. From the user's own Claude Code transcripts, finds where every token goes and which ones should not be there at all; runs only through /token-lab:token-lab.
disable-model-invocation: true
---

# Token map

You run a read-only report that measures the user's own usage and explain the result in plain words, then go on to review their files with them. You never change a setting on your own.

Speak the user's language: the report and these instructions are in English; translate what you say. Assume the user is not technical.

## 1. Run the report

Before running, say in one sentence: "I'll read your Claude Code history now; it takes about a minute (the first time up to three). Claude Code will ask to run a command: choose Yes."

Run it with a 600000 ms timeout. If the user has a rules file (`~/.claude/token-lab.toml`), add it:

```
uvx --from "${CLAUDE_PLUGIN_ROOT}" tokenlab why [--rules ~/.claude/token-lab.toml]
```

- If `uvx` is not found: "If you installed token-lab just now, close Claude Code completely and open it again; it cannot see the newly installed `uv` yet." If that is not the case, give the install command for the user's OS and the same advice, then stop:
  - Windows (PowerShell): `winget install astral-sh.uv`
  - macOS / Linux (Terminal): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- The report only reads: the transcripts under `~/.claude/projects`, `~/.claude/agents`, `~/.claude/skills` and the `MEMORY.md` files the transcripts name. It makes no network calls; only the first run lets `uvx` download the package and, if needed, Python.
- It keeps one line of numbers per run in `~/.claude/token-lab/history.jsonl` for its trend (no names, paths or text); add `--no-history` if the user does not want it.
- If the output says there is no Claude Code history yet ("No Claude Code history found" or "No Claude Code requests yet"), say: "There is not enough history yet; use Claude Code for a few days and run `/token-lab:token-lab` again." Then stop.
- Do not paste the output into the chat; read it and summarise.
- If the user asks whether a change helped, run the report twice: with `--until <YYYY-MM-DD>` (the day of the change; transcripts up to then, files on disk as they are now) and without. Compare the two Summary tables and the bold sentences at the top.

## 2. First view: short and plain

The report's top, "Summary" and "First step" sections are written for this. Explain every technical term the first time in one short everyday sentence (e.g. "context: everything Claude re-reads with each of your messages").

1. If a bold "Warning:" line or "Only N days of history" line is at the top, say it first.
2. The two bold sentences at the top, with their numbers, in everyday words. If a "Trend" table follows, one sentence on what changed since the last run and since about a month ago.
3. The three angles with the largest share of cost, in the Summary's order: what was found and what to do, one line each. Then one line: the rest is small or fine.
4. The Cache and Hooks lines are observations; do not promise a sure saving. Say in one sentence how much of Claude Code's own cost counter the report sees (the first line of section 3), e.g. "The report sees 88% of what Claude Code counts; the rest is background work it cannot see, so real numbers are a bit higher." If section 3 says there is no counter, say the numbers could not be checked against it.
5. End with one question, so the user goes on to their files: "Next I can go through your most expensive files (CLAUDE.md, rules, memory, skills, agents): what can be cut or moved, and what is missing, such as a hook, a rule or a memory line. Shall I go on? Or we can start with the first step above." Yes → step 4. The first step → step 3. No or stop → step 5.

Give the agent details (the general-purpose share, the "Agent candidates" table, section 5) only if the user asks about agents or the Agents angle is in the top three. Open a table only if the user asks.

## 3. Report items, one at a time

- If the user picks an item, first show the target: which file or setting, the evidence in the report, its share of cost.
- When an item is done or the user skips it, offer the next item with the largest share.
- When no item is left or the user wants to move on, offer the file review: "Shall I go through your most expensive files now?" Yes → step 4. No or stop → step 5.

Before you change a file or a setting, read the standard that applies and write by it:

- Every change: `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/placement.md`. Decide where a line or rule goes with its decision table and its "Report finding → usual place" table.
- A new or changed agent: `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/agent.md`. Use the tool and model columns of the candidate in section 5.
- A memory file or `MEMORY.md`: `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/memory.md`.

While changing:

- First show the file you will touch and the full diff, and get a yes.
- Before changing, back the file up in the same folder with the suffix `.bak-token-lab-<YYYYMMDD-HHMMSS>`; never overwrite an existing backup. A backup also carries secrets such as `env` in `settings.json`: do not print it, tell the user only its path.
- In JSON files such as `settings.json`, add only the key in question; leave the rest of the file alone. In the diff, show only the lines of the changed key and mask `env` values as `***`.
- `skillOverrides` does not hide a plugin's skills; add `disable-model-invocation: true` to that skill's `SKILL.md` frontmatter instead, and it is needed again after the plugin updates. Tell the user this.
- Never apply an item on your own; each one needs its own yes.

## 4. Going on: the file review

Read `${CLAUDE_PLUGIN_ROOT}/skills/optimize/SKILL.md` and follow it from its step 1, in this session. That skill cannot be invoked on its own; you run its steps here, and the user does not need to type `/token-lab:optimize`. Its step 7 closes the session; do not repeat the lines of step 5 here.

## 5. Closing

The last two sentences, in the user's language, in this order:

- "Run `/token-lab:token-lab` again in a week; the Trend at the top will show what changed."
- "Problems or ideas: https://github.com/merttrkr/claude-token-kit/issues — do not paste conversation text, file paths or company information."
