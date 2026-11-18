# Token map (token-lab)

**English** · [Türkçe](README.tr.md)

> **Trial version (0.1).** Works on Windows, macOS and Linux. Tested on Windows and Linux so far (not yet on macOS), with data from a few people. If you see a problem or an odd result, post it on the [Issues](https://github.com/merttrkr/claude-token-kit/issues) page.

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

- **Read-only.** It changes no setting and no file. It reads:
  - the logs under `~/.claude/projects`
  - `~/.claude/agents` and `~/.claude/skills`
  - the memory indexes (`MEMORY.md`) named in the logs
- **Sends nothing.** The report never leaves your machine. Only the first run installs the `uv` package and, if needed, downloads Python.
- **Leaves nothing running.** No hook, no background program, no scheduled task.
- If you want to apply a finding, tell Claude; it shows the file and the diff and applies it only after you say yes.

## Install

Easiest: give Claude Code the link to this page and say **"install this"**. Or one command.

Windows (PowerShell):

```powershell
irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.ps1 | iex
```

macOS / Linux:

```sh
curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.sh | sh
```

[`install.ps1`](install.ps1) and [`install.sh`](install.sh) only do this: check that Claude Code is installed, install `uv` if missing (`winget` on Windows, uv's own installer on macOS/Linux), then add and install this plugin. It touches no other setting; it is short, so you can read it.

Then restart Claude Code and type `/token-lab:token-lab`.

> **Install steps for Claude** (if the user asked you to install this repo):
> 1. On Windows run in PowerShell: `irm https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.ps1 | iex`. On macOS or Linux run: `curl -fsSL https://raw.githubusercontent.com/merttrkr/claude-token-kit/main/install.sh | sh`
> 2. If the output has no "Installed", tell the user the error exactly as printed and stop.
> 3. Tell the user: "Installed. Type `/reload-plugins` in this session or restart Claude, then type `/token-lab:token-lab`."
> 4. Change no setting other than the install.

To install by hand: first [uv](https://docs.astral.sh/uv/) (Windows: `winget install astral-sh.uv`; macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`), then from Claude Code:

```
claude plugin marketplace add https://github.com/merttrkr/claude-token-kit
claude plugin install token-lab@token-kit
```

Then type `/token-lab:token-lab` in Claude.

To only see the report, without installing:

```
uvx --from git+https://github.com/merttrkr/claude-token-kit tokenlab why
```

## How to read the report

The report text is in Turkish for now. `/token-lab:token-lab` has Claude summarize it in your language.

- **Top:** two sentences with the main numbers, then a **Summary** table with one line per angle (tool output, cache, CLAUDE.md, memory, tools and MCP, skills, agents, hooks), largest share of cost first, and the **First step**.
- **Section 1:** the part sent again on every request, and its share.
- **Section 2:** what should not be there, sorted by weekly cost.
  - Cost is "input-equivalent tokens". A token read from cache counts about 0.1, a token written to cache 1.25–2 (Claude Code's price ratios).
  - The effect on a subscription quota was not measured.
- **"Already looks fixed":** items no longer seen in the last 2 days of sessions. Not suggested.
- **Section 3:** what the report cannot see. Session titles and compaction requests are not written to the logs.
- **Section 4:** the files carried the most. If you want, Claude reads them and finds lines you don't need. This part is model judgment.
- **Section 5:** agent work. Which repeated work could move to a named agent, which agent is unused.

How far back can the report look? Claude Code deletes old logs based on the `cleanupPeriodDays` setting (default 30 days). That setting is yours.

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

The report itself writes nothing. What stays is `uv` and its cache (Python and packages); remove them with `uv cache clean` if you want.

## Development

```
uv sync
uv run pytest -q
uv run ruff check . && uv run mypy src
```
