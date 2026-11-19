---
name: optimize
description: File review. Goes through the user's most expensive loaded files (CLAUDE.md, rules, memory, skills, agents) against the placement standard, proposes delete, shorten, move and add changes and applies each after a yes; runs through /token-lab:optimize or after the token-lab report.
disable-model-invocation: true
---

# File review

You read the pieces that enter the user's context, most expensive first, against the placement standard, propose changes line by line and apply them with a yes. How this differs from the report (`/token-lab:token-lab`): the report measures cost, this review reads the lines. You agree the scope with the user. You never change a file on your own.

Speak the user's language: the inventory and these instructions are in English; translate what you say. Assume the user is not technical; explain a term the first time in one everyday sentence.

## 1. Build the inventory

If the user has a rules file (`~/.claude/token-lab.toml`), add it:

```
uvx --from "${CLAUDE_PLUGIN_ROOT}" tokenlab inventory [--rules ~/.claude/token-lab.toml]
```

- The command prints a folder path. Read `inventory.md` in it; you use `system-prompt.md` in step 3, and `pieces.md` (every piece) if the scope widens.
- If `inventory.md` says "No Claude Code requests yet", tell the user and stop.
- If `uvx` is not found, say "Install uv first, then reopen Claude." and give the command for the user's OS, then stop (Windows: `winget install astral-sh.uv`; macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- The inventory only reads and makes no network calls. Its sections:
  1. **Pieces:** each piece's file, size, weekly cost and use.
  2. **Stale references:** paths that are not on disk. A script result, sure.
  3. **Repeated lines:** sentences the system prompt already says, and pairs of files that share sentences. A script result, sure.
  4. **Could be added:** recurring tool errors, files re-read across sessions, where the corrections are. These are candidates; reading decides.

## 2. Agree the scope

In the user's language, short:

1. How many pieces there are, which are the 10 most expensive (section 1), how many sure findings sections 2–3 have, how many candidates section 4 has.
2. One question in two parts: "I'll start with the 10 most expensive files and the sure findings. Shall I also look at the places in your past conversations where errors and corrections repeat, to suggest what to add?" The user may widen the scope (from `pieces.md`), narrow it, or refuse the conversation reading; do step 4 only if they say yes.
3. If the user asks what the review costs, give a rough estimate: the characters to read (the pieces, plus `placement.md` and `system-prompt.md` once per reader, plus conversation excerpts) ÷ 4 ≈ tokens, output extra. Do not bring it up otherwise.

Pieces that come from a plugin (under `~/.claude/plugins/cache/...`) go back to their old form when the plugin updates. For them, propose hiding or turning off rather than editing, and tell the user so.

## 3. Read and judge

First read `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/placement.md`. Then, for each piece in scope:

- Read the file. Judge each line or block with the standard's six questions; for the global `~/.claude/CLAUDE.md` also apply the six conditions.
- Answer "Does Claude do this already?" by looking at `system-prompt.md`: if the same thing is written there, the line is needless. If it is not there, do not lean on a default you think you know; say "could not be verified".
- Use the lines in sections 2 and 3 of the inventory as facts; do not argue them again.
- If more than 3 pieces are in scope, split the reading across parallel read-only agents (one per piece or small groups). Give each agent the piece's path, the paths of `placement.md` and `system-prompt.md`, and the inventory lines for that file. Agents change no file; they only return the table below.

When the readings are done, you, in the main session, read the returned tables together: add findings for rules that contradict each other across files and for lines that say the same thing in other words (the script only finds identical sentences).

One row per finding:

| piece:line | what | question it fails | action (delete / shorten / move / merge) | where to | certainty (sure: script / judgment: model) |
|---|---|---|---|---|---|

## 4. Judge what could be added

- **Recurring tool errors:** open the example places in the inventory (search the transcript file for that time) and see the error and the command that produced it. If a pattern a machine can check keeps coming back, propose a hook or a permission rule (the hook row of the placement table); otherwise a one-line rule in the right place.
- **Files re-read across sessions:** if needed in every session, a memory line or a pointer in CLAUDE.md ("read Y before X"); if needed only for one job, a reference in that job's skill.
- **Corrections:** read each listed message in its transcript together with the Claude answer before it. Group the recurring corrections and propose a rule in the cheapest place where it triggers. Add nothing for one-off ones.
- Copy no transcript text anywhere; only summarise it briefly in the suggestion.

## 5. Show one list

- Merge all findings into one list. Order: cost effect (the piece's weekly cost × the share of lines affected); on a tie, sure findings first.
- Show the first 10 in plain words: what, why, where it goes, its effect. Explain a technical term in one word.
- One question: "Shall we start with the first one?"

## 6. Apply one at a time

One suggestion at a time, each with its own yes:

- First show the file you will touch and the full diff, and get a yes.
- Before changing, back the file up in the same folder with the suffix `.bak-token-lab-<YYYYMMDD-HHMMSS>`; if a backup with that name exists, do not overwrite it, pick a new name. A backup also carries secrets such as `env` in `settings.json`: do not print it, tell the user only its path.
- For `settings.json`, do not show the full file diff: show only the lines of the changed key; mask `env` and similar values as `***`.
- For a move, first add to the target, then delete from the source; show both in the same diff.
- In JSON files such as `settings.json`, add only the key in question; leave the rest of the file alone.
- For a new or changed agent follow `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/agent.md`; for memory, `${CLAUDE_PLUGIN_ROOT}/skills/token-lab/references/memory.md`.
- When an item is done or the user skips it, offer the next one. Stop if the user says stop or no suggestion is left.

## 7. Closing

- Delete the inventory folder (it holds the system prompt and file paths) and tell the user it is deleted.

The last two sentences, in the user's language:

- "Run the report again with `/token-lab:token-lab` in a week; the cost of the pieces you changed should have dropped."
- "Problems or ideas: https://github.com/merttrkr/claude-token-kit/issues — do not paste conversation text, file paths or company information."
