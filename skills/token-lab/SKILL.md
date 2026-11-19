---
name: token-lab
description: Token map. From the user's own Claude Code transcripts, finds where every token goes and which ones should not be there at all; runs only through /token-lab:token-lab.
disable-model-invocation: true
---

# Token map

You run a read-only report that measures the user's own usage and explain the result in plain words. You never change a setting on your own.

Speak the user's language: the report and these instructions are in English; translate what you say.

## 1. Run the report

If the user has a rules file (`~/.claude/token-lab.toml`), add it:

```
uvx --from "${CLAUDE_PLUGIN_ROOT}" tokenlab why [--rules ~/.claude/token-lab.toml]
```

- If `uvx` is not found, say "Install uv first, then reopen Claude." and give the command for the user's OS, then stop:
  - Windows: `winget install astral-sh.uv`
  - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
- The report only reads: the transcripts under `~/.claude/projects`, `~/.claude/agents`, `~/.claude/skills` and the `MEMORY.md` files the transcripts name. On a long history it takes about a minute.
- The report keeps one line of numbers per run in `~/.claude/token-lab/history.jsonl` for its trend (no names, paths or text); add `--no-history` if the user does not want it.
- The report makes no network calls; only the first run lets `uvx` install the package and, if needed, download Python.
- Do not paste the whole output into the chat; read it and summarise.
- If the user asks whether a change helped, run the report twice: with `--until <YYYY-MM-DD>` (the day of the change; transcripts up to then, files on disk as they are now) and without. Compare the two Summary tables angle by angle and the two bold sentences at the top.

## 2. First view: short, every angle

For someone who is not technical. The report's "Summary" and "First step" sections are written for this:

1. Say the two bold sentences at the top of the report, with their numbers. If a "Trend" table follows, say in one sentence what changed since the last run and since about a month ago (fixed part per request, cuttable waste). If a bold "Warning:" line follows them, say it first: some records could not be read and the numbers may be low.
2. One line per angle of the "Summary" table, in the table's order: what was found, its share of cost, what to do. Group the "no issue" angles into one line at the end. Explain a technical term in one word (e.g. cache, context).
3. In the Agents line, say the general-purpose share (and the trend of the last days, if shown). From the "Agent candidates" table at the end of section 2, name the jobs whose verdict is "agent candidate" and where they run (main session or general-purpose). From section 5: name the largest new candidate in "Recurring generic jobs", the largest job an existing agent missed in "Generic calls an existing agent covers", and any verdict other than "keep" in "Existing agents" (e.g. retire, merge, check grants, tighten contract).
4. The Cache and Hooks lines are observations; do not promise a sure saving.
5. Say once that cost is "input-equivalent tokens" and that the effect on a subscription quota is not measured. Add the first line of section 3: how much of Claude Code's own cost counter the transcripts hold.
6. Say in one sentence what the report cannot measure: what is inside the files (needless, stale or misplaced lines) and what is missing (a hook, a memory line, a rule). A full optimization can look at these after the report items; it spends tokens.
7. End with the "First step" and one question: "Shall we start with this?"

Do not paste the whole output; open a table only if the user asks.

## 3. One step at a time

One item at a time:

- If the user says yes, first show the target: which file or setting, the evidence in the report, its share of cost.
- When an item is done or the user skips it, offer the next item with the largest share and ask one question again.
- When no item that can be cut is left, or the user skips the rest, ask one question: "Shall I go on to the full optimization that reads your files line by line? It spends tokens; we agree the scope together." If yes, go to the last paragraph of step 4.
- If the user says stop or does not want the optimization, go to step 5.

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

## 4. Optional: reading files

If the user wants, read the files in section 4 of the report. Judge every line with the six questions in `references/placement.md`; for the global `~/.claude/CLAUDE.md` also apply the six conditions. Give each suggestion with its line number and action (delete, shorten, move: where).

This part is model judgment; tell the user so. Apply deletes or moves with a yes, as in step 3.

Full optimization: when the user says yes to the question at the end of step 3, or at any point asks for every piece (CLAUDE.md, rules, memory, skills, agents) to be reviewed line by line and for what is missing (hook, memory, rule) to be suggested, read `${CLAUDE_PLUGIN_ROOT}/skills/optimize/SKILL.md` and follow it from its step 1. That skill cannot be invoked on its own; you read its file and run its steps in this session, and the user does not need to type `/token-lab:optimize`. Its step 7 closes the session; do not repeat the lines of step 5 here.

## 5. Closing

The last two sentences, in the user's language, in this order:

- "Run `/token-lab:token-lab` again in a week; the share of what you applied should have dropped."
- "Problems or ideas: https://github.com/merttrkr/claude-token-kit/issues — do not paste conversation text, file paths or company information."
