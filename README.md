# Token map (token-lab)

**English** · [Türkçe](README.tr.md)

> **Trial version (0.2).** Works on Windows, macOS and Linux. Tested on Windows and Linux so far (not yet on macOS), with data from a few people. If you see a problem or an odd result, post it on the [Issues](https://github.com/merttrkr/claude-token-kit/issues) page.

Claude Code sends the model a lot with every request: tool definitions, skill and agent lists, CLAUDE.md, memory files, earlier tool output. This tool looks at **your own** conversation logs and answers two questions:

1. Where do my tokens go?
2. Which of them should not be there at all?

Example findings:

- "41 skills in the list, 3 were used; 4k tokens on every request."
- "9 tools of one MCP server were never called in 30 days."
- "The cache went cold 277 times: after a long pause the whole context was written again."
- "One file was read from the start 22 times in the same session."

Each finding shows what it cost, the evidence, and a one-sentence fix.

## Safety

- **The report is read-only.** It changes no setting and none of your files. Its one write is its own history, `~/.claude/token-lab/history.jsonl`: a line of numbers per run (no names, paths or text) for the trend at the top of the next report. Turn it off with `--no-history`; delete the file to forget it. `/token-lab:optimize` changes a file only after you see the diff and say yes. The report reads:
  - the logs under `~/.claude/projects`
  - `~/.claude/agents` and `~/.claude/skills`
  - the memory indexes (`MEMORY.md`) named in the logs
- **The tool sends nothing.** It makes no network calls; only the first run installs the `uv` package and, if needed, downloads Python. When you run it through `/token-lab:token-lab` or `/token-lab:optimize`, Claude reads the report (and, in optimize, the files under review) like any file in your session, so it goes wherever your Claude conversation goes.
- **Leaves nothing running.** No hook, no background program, no scheduled task.
- If you want to apply a finding, tell Claude; it shows the file and the diff and applies it only after you say yes.

## Install

Easiest: give Claude Code the link to this page and say **"install this"**. Or one command.

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.4/install.ps1 | iex
```

macOS / Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.4/install.sh | sh
```

[`install.ps1`](install.ps1) and [`install.sh`](install.sh) only do this: check that Claude Code is installed, install `uv` if missing (`winget` on Windows, uv's own installer on macOS/Linux), then add and install this plugin, and turn on auto-update for it (the same switch as `/plugin` → Marketplaces → token-kit) unless you turned it off before. It touches no other setting; it is short, so you can read it.

Then restart Claude Code and type `/token-lab:token-lab`.

**Updates:** with auto-update on, Claude Code fetches a new version by itself within a few minutes of a session's start and says "Plugin updated · Run /reload-plugins". To update now, run the install command again. To turn auto-update off: `/plugin` → Marketplaces → token-kit.

> **Install steps for Claude** (if the user asked you to install this repo):
> 1. On Windows run in PowerShell: `irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.4/install.ps1 | iex`. On macOS or Linux run: `curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/v0.2.4/install.sh | sh`
> 2. If the output has no "Installed", tell the user the error exactly as printed and stop.
> 3. Tell the user: "Installed. Type `/reload-plugins` in this session or restart Claude, then type `/token-lab:token-lab`."
> 4. Change no setting other than the install.

To install by hand: first [uv](https://docs.astral.sh/uv/) (Windows: `winget install astral-sh.uv`; macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`), then from Claude Code:

```
claude plugin marketplace add https://github.com/merttrkr/claude-token-kit
claude plugin install token-lab@token-kit
```

Then type `/token-lab:token-lab` in Claude. For auto-update, turn it on in `/plugin` → Marketplaces → token-kit.

To only see the report, without installing:

```
uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why
```

## How to read the report

The report is in English. `/token-lab:token-lab` has Claude summarize it in your language.

- **Top:** two sentences with the main numbers, then a **Summary** table with one line per angle (tool output, cache, CLAUDE.md, memory, tools and MCP, skills, agents, hooks), largest share of cost first, and the **First step**.
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

## Full optimization: `/token-lab:optimize`

The report measures; this mode reads. Claude goes through every piece that enters your context (CLAUDE.md files, rules, memory, skills, agents), most expensive first, against the placement standard, and proposes for each line: delete, shorten, move (and where) or merge. It also proposes what to add: a hook for a tool error that keeps coming back, a memory line for a file you read in every session, a rule for a correction you keep making.

- A script first lists what it can check for sure: paths that no longer exist, sentences the system prompt already says, files that share the same lines.
- The report offers it once its own items are done; you can also start it directly.
- It spends tokens: Claude tells you the estimate and asks how many pieces to read.
- Changes are applied one at a time, each with a diff, a backup and your yes.

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

```
claude plugin uninstall token-lab@token-kit
claude plugin marketplace remove token-kit
```

What stays: the report's history, `~/.claude/token-lab/history.jsonl` (delete the `~/.claude/token-lab` folder), and `uv` with its cache (Python and packages; remove them with `uv cache clean` if you want).

## Development

```
uv sync
uv run pytest -q
uv run ruff check . && uv run mypy src
```

Supported commands are `why` and `inventory`; the others are research tools, kept but not supported. See [CONTRIBUTING.md](CONTRIBUTING.md), [CHANGELOG.md](CHANGELOG.md) and [SECURITY.md](SECURITY.md).
