# Where a piece of context belongs

Read this before you change, move, add or delete anything the report points at: a CLAUDE.md line, a rule, a memory line, a skill, an agent, a setting, a hook.

Every line that enters the context must earn its place. Read each piece against these questions, in order.

## Questions

1. **Does it change behavior?** Without it, would Claude do something else? If not, delete.
2. **Is it concrete?** "In case X, do Y" can be checked; "be careful" cannot. Vague → rewrite or delete.
3. **Is it still true?** The files, flags, tools and agents it names still exist. Check them.
4. **Is it said once?** The same rule in Claude Code's own prompt, another CLAUDE.md, a skill, a memory or a hook is a duplicate. Keep it in the cheapest place that still fires when needed.
5. **Right reader?** The main conversation only, a subagent, or one workflow. CLAUDE.md is read by the main conversation and by subagents (the built-in Explore and Plan agents skip it).
6. **Right place?** The cheapest place that is loaded when the line is needed (table below).

## Decision table

| Place | Paid when | Reader | Belongs here |
|---|---|---|---|
| nowhere (delete) | never | — | unused, duplicate, or what Claude does by default anyway |
| `settings.json` permissions `deny` | 0 model tokens | Claude Code itself | "never touch X": a tool or path ban the harness enforces (`ask` only asks for confirmation, it does not block) |
| hook or script | 0 model tokens; its message only when it fires | the thread that hit it | a rule a machine can check; what to do when blocked goes in the block message |
| path-scoped rule (`.claude/rules/*.md` with `paths:`) | when a matching file is read or written | any thread that reads the file | conventions tied to a file type or folder |
| memory file | when read | whoever reads it | an occasional fact |
| referenced file ("before X, read …" in CLAUDE.md) | when the trigger fires | whichever thread hits the trigger | a procedure several kinds of thread need at one moment |
| `MEMORY.md` index line | every session, main | main | a pointer and when to read it, nothing more |
| skill body | when invoked | the thread that invokes it (main or a subagent) | an occasional procedure |
| skill or agent description | every request | main | when to call it, nothing more |
| agent definition body | each spawn of that agent | that agent only | rules only that agent needs |
| agent | each spawn: cold start plus re-reads | — | reads a lot, returns little; a cheaper model or parallel work |
| project CLAUDE.md | every request | main and subagents in that project | short invariants every thread in that project needs |
| global `~/.claude/CLAUDE.md` | every request | main and subagents, every project | only what passes the six conditions below |

Actions on a piece: **delete** (fails questions 1–4), **edit** (vague or long → concrete and short), **move** (wrong reader or place → the cheapest place above that still fires), **add** (a correction that keeps recurring → the cheapest place that fires).

## Global CLAUDE.md: six conditions

`~/.claude/CLAUDE.md` rides in every request of every thread in every project. A line belongs there only if all six hold:

1. **Every project.** Otherwise → that project's CLAUDE.md.
2. **No trigger moment.** It applies in nearly every message. Otherwise → skill, hook or a referenced file.
3. **Changes the default.** Claude Code does not already do it. Otherwise → delete.
4. **Explicit about conflicts.** If it overrides Claude Code's default, it says so plainly.
5. **Not machine-checkable.** Otherwise → hook.
6. **Concrete.** Reading one reply shows whether it was followed.

## Report finding → usual place

| Finding in the report | Usual action |
|---|---|
| long sessions (observation) | not a file: start a new session per task or stage, carry on from a short handoff note, `/compact` earlier; say the saving is not measured |
| an agent or skill listed twice under the same name | hide the copy that is not used (plugin skill: `disable-model-invocation: true`; agent: `Agent(<name>)` in `permissions.deny`) |
| MCP servers offering the same tools | ask whether both are needed (another environment is a reason); if not, disable one server |
| a tool never called | `permissions.deny` in `settings.json` (not `ToolSearch`: denying it loads every deferred tool in full) |
| MCP tools never called | `permissions.deny` with the tool name (`mcp__<server>__<tool>`) or the whole server (`mcp__<server>`), or disable the server |
| own skill never used | `skillOverrides` in `settings.json`: `"user-invocable-only"` hides it from Claude but keeps `/name`; `"name-only"` keeps it listed without its description; `"off"` hides it from both. Ask the user which |
| plugin skill never used | `disable-model-invocation: true` in that skill's frontmatter (lost when the plugin updates), or uninstall the plugin |
| agent never used | own agent: delete its file; built-in: `permissions.deny` with `Agent(<name>)` |
| agent never used, but the only way to a tool the main session lacks (observation) | keep it, unless the user never needs that tool (e.g. `web-fetch` when the main session has no `WebFetch`) |
| a CLAUDE.md or rule file that a hook or plugin rewrites (an `AUTO-GENERATED` header, often git-excluded) | an edit does not last: if the user does not need it, add its path to `claudeMdExcludes` in `settings.json` (glob patterns matched against the absolute path, e.g. `"/home/me/repo/.claude/rules/octo-*.md"`; any settings layer, the lists merge; a managed CLAUDE.md cannot be excluded) |
| recurring general-purpose job, or an outside system the main session reads itself again and again ("Agent candidates" table, verdict "agent candidate") | a named agent, written with `agent.md` |
| general-purpose job an existing agent covers | sharpen that agent's `description` so it gets picked (`agent.md`, point 1) |
| verdict in "Existing agents" (retire, merge, narrow tools, errors) | act on that verdict with `agent.md`; retiring = delete its file after the user's yes |
| big tool output, the same file read again and again | a step in the skill or rule where those reads happen ("read X with offset/limit"); a CLAUDE.md line only if it passes the questions above |
| memory index lines never read | archive or merge, with `memory.md` |
| memory files written but never read back | first narrow the rule that makes Claude write them; then archive, with `memory.md` |
| CLAUDE.md and rule files | read line by line with the questions above; delete, shorten or move each line |
| skill bodies re-added after compaction | shrink the skill: move details into reference files it reads only when needed |
| hook text | shorten the hook's message, or emit it only when it is needed |
| cache cooling | an observation; no file change |

## Size

No invented thresholds. A piece's ceiling is its size today, and it only goes down. Growth needs a reason.

## Process

The report measures → Claude reads against these questions → the user approves the diff → the report is run again a week later to see the share drop.
