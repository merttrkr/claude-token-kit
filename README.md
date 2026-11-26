# Token map (token-lab)

**English** · [Türkçe](README.tr.md)

See where your Claude Code tokens go, and cut the ones you do not need. token-lab reads your own Claude Code history on your computer and shows, in plain numbers, what Claude re-reads with every message, what is never used and what to change. Then it goes through your instruction files with you and changes them only after your yes.

> **Trial version (0.2).** Works on Windows, macOS and Linux; tried by a few people on their own computers. Problems or odd results: [Issues](https://github.com/merttrkr/claude-token-kit/issues).

What it finds, for example:

- "41 skills are listed in every message and you used 3: 4 thousand tokens each time."
- "9 tools of one connected service (MCP) were never used in 30 days."
- "After long breaks, Claude re-read the whole conversation from scratch 277 times."
- "One file was read from the start 22 times in one session."

Each finding shows what it costs, the evidence and a one-sentence fix. Numbers are tokens, not money: how they count against a subscription's limits is not measured.

## Safety

- **Reads only on your computer:** your Claude Code history under `~/.claude/projects`, `~/.claude/agents`, `~/.claude/skills` and the memory indexes (`MEMORY.md`) the history names.
- **Sends nothing.** token-lab itself makes no network calls. Downloads happen only when you install (`uv`) and on the first run (token-lab itself and, if needed, Python). Inside Claude Code, Claude reads the report like any file in your conversation, so the report goes where your Claude conversation goes.
- **Changes nothing without your yes.** Your settings, CLAUDE.md, memory, skills and agents change only after you say yes: to each change, or once to "do it all and show me at the end"; a backup is kept next to the file (`.bak-token-lab-…`).
- **Its own files:** one line of numbers per run in `~/.claude/token-lab/history.jsonl` for the trend (no names, paths or text; `--no-history` turns it off), and temporary files it deletes. The full list is in [SECURITY.md](SECURITY.md).
- **Leaves nothing running:** no hook, no background program, no scheduled task.

## Install (about 5 minutes)

1. **Open a terminal window** (not Claude Code):
   - Windows: press Start, type **PowerShell**, open it.
   - macOS: press Cmd+Space, type **Terminal**, open it.
2. **Paste this command and press Enter.**

   Windows:

   ```powershell
   irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.7/install.ps1 | iex
   ```

   macOS / Linux:

   ```sh
   curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.7/install.sh | sh
   ```

3. **Wait for "Installed."** The first time takes 1–3 minutes. If anything else appears, the message says what to do.
4. **Close Claude Code completely** (every window) and open it again.
5. **In Claude Code, type `/token-lab:token-lab`** and press Enter. Claude asks to run a command: choose **Yes**. The report takes about a minute; then Claude explains it and offers to go through your files.

What the install does: checks that Claude Code is there, installs `uv` (a small tool token-lab runs on) if it is missing, adds token-lab to Claude Code and turns on its automatic updates. Nothing else; the scripts are short: [`install.ps1`](install.ps1), [`install.sh`](install.sh).

You can also give Claude Code the link to this page and say **"install token-lab"**; then do steps 4 and 5.

**Updates** arrive by themselves a few minutes after you start Claude Code, which then says "Plugin updated · Run /reload-plugins": type `/reload-plugins`. To update right away, paste the install command again. To see your version: in Claude Code type `/plugin` and open **Installed**; the report also shows it in its first line.

> **For Claude Code, when a user asks it to install token-lab:**
> 1. On Windows run in PowerShell: `irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.7/install.ps1 | iex`. On macOS or Linux run: `curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.7/install.sh | sh`
> 2. If the output has no "Installed", tell the user the message exactly as printed and stop.
> 3. Tell the user: "Installed. Close Claude Code completely, open it again, then type `/token-lab:token-lab`."
> 4. Change no setting other than the install.

**Install by hand,** in the same terminal window (not in Claude Code): install [uv](https://docs.astral.sh/uv/) (Windows: `winget install astral-sh.uv`; macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`), close and reopen the window, then:

```
claude plugin marketplace add https://github.com/merttrkr/claude-token-kit
claude plugin install token-lab@token-kit
```

Then steps 4 and 5. Automatic updates: in Claude Code type `/plugin`, open Marketplaces, choose token-kit.

**Only the report, without installing** (needs `uv` and `git`):

```
uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why
```

## How to read the report

The report is in English. `/token-lab:token-lab` has Claude summarize it in your language.

- **Top:** two sentences with the main numbers, then a **Summary** table with one line per angle (long sessions, cache, tool output, CLAUDE.md, memory, tools and MCP, skills, agents, hooks; long sessions and cache are observations, not counted as cuttable), largest share of cost first, and the **First step**.
- **Section 1:** the part sent again on every request, and its share.
- **Section 2:** what should not be there, sorted by weekly cost. It ends with an **agent candidates** table: recurring work (in general-purpose agents, or outside systems the main session reads itself) measured against the agent standard, with the estimated weekly saving.
  - Cost is "input-equivalent tokens". A token read from cache counts about 0.1, a token written to cache 1.25–2 (Claude Code's price ratios).
  - The effect on a subscription quota was not measured.
- **"Already looks fixed":** items no longer seen in the last 2 days of sessions. Not suggested.
- **Section 3:** what the report cannot see. Session titles and compaction requests are not written to the logs.
- **Section 4:** the files carried the most. If you want, Claude reads them and finds lines you don't need. This part is model judgment.
- **Section 5:** agent work. Which repeated work could move to a named agent, which agent is unused.

How far back can the report look? Claude Code deletes old logs based on the `cleanupPeriodDays` setting (default 30 days). That setting is yours.

**Progress:** from the second run on, a **Trend** table at the top compares the fixed part per request, the cuttable waste and each angle's share of cost with the last run and with a run about a month earlier. The first run also measures an earlier point once (two weeks back, or halfway through a shorter history; nothing under two days), so the trend shows from day one; it takes a little longer.

**Did a change help?** `uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why --until 2026-11-10` builds the report from the transcripts up to the start of that day (UTC); files on disk (memory, agents, skills) are read as they are now. Compare its Summary with today's; or ask Claude in `/token-lab:token-lab` to compare before and after a date.

## File review: `/token-lab:optimize`

The report measures; this review reads. Claude goes through the files that enter your context (CLAUDE.md files, rules, memory, skills, agents), the 10 most expensive first (you can widen it), against the placement standard, and proposes for each line: delete, shorten, move (and where) or merge. It also proposes what to add: a hook for a tool error that keeps coming back, a memory line for a file you read in every session, a rule for a correction you keep making.

- A script first lists what it can check for sure: paths that no longer exist, sentences the system prompt already says, files that share the same lines.
- The report leads into it: after the summary Claude offers to go on with your files. You can also start it directly.
- Claude asks which files to start with and whether to look at your past conversations for what to add.
- First Claude asks how to apply: one at a time (each with a diff, a backup and your yes) or all at once, with every file backed up and every change shown at the end.
- It says up front roughly what the review costs, and lets you read in this session (cheaper) or with parallel readers (faster).

## Your own rules

Thresholds and area names are in `src/tokenlab/rules.py`. To change them, write `~/.claude/token-lab.toml`:

```toml
[why]
min_weekly_tokens = 50000

[[areas]]            # folders used to group your work
name = "specs"
path = "/specs/"

[external]           # MCP servers that point at the same system
"mcp-atlassian" = "jira"
```

## Feedback

For a problem or an idea, use this repo's **Issues** page. Don't paste conversation text, file paths or company information; issues are public.

## Uninstall

In a terminal window (not in Claude Code):

```
claude plugin uninstall token-lab@token-kit
claude plugin marketplace remove token-kit
```

What stays, if you want it gone too:

- The report's history: delete the folder `~/.claude/token-lab`.
- Backups the file review made next to changed files: `*.bak-token-lab-*`.
- `uv`, with the Python and packages it downloaded: `uv cache clean`, then remove uv itself (Windows: `winget uninstall astral-sh.uv`; macOS/Linux: see [uv's docs](https://docs.astral.sh/uv/getting-started/installation/#uninstallation)).

## Development

```
uv sync
uv run pytest -q
uv run ruff check . && uv run mypy src
```

Supported commands are `why` and `inventory`; the others are research tools, kept but not supported. See [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md) and [SECURITY.md](SECURITY.md).
