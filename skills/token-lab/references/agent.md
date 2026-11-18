# Writing a subagent

Read this before you create or change an agent in `~/.claude/agents/` or a project's `.claude/agents/`. Input from the report, section 5: "Recurring generic jobs" (the job, calls, sessions, tools used, model, tool errors), "Generic calls an existing agent covers", "Existing agents" (verdicts) and "Calls by agent" (threads with a tool error).

## Gate: should this be an agent at all?

Write a file only when all three hold. Otherwise stop and say which failed.

- **Recurring.** The same job across sessions (the report's calls and sessions columns). A one-off → an inline Agent call with a prompt, no file.
- **Needs the model to steer.** A fixed sequence of steps → a script or a skill, not an agent.
- **Gains from a fixed scope.** A locked tool set and a fixed model earn their keep. If it would inherit everything, inline delegation is simpler.

## Six things that go wrong, and the fix

1. **Never picked.** Claude picks an agent by its `description`. Write it as a trigger: "Use proactively when …", plus at least one "Not for …" for a nearby job that belongs elsewhere.
2. **Overreaches.** Without `tools` the agent inherits every tool, MCP included. Set `tools` as an allowlist: start from the tools column of the report, keep only what the job needs. Read-only jobs get no `Edit`, `Write` or MCP write tools.
3. **Wrong model.** Without `model` it inherits the parent's model, often the most expensive one. Pin it: `haiku` for lookups and digests, `sonnet` for writing and mapping, `opus` only for judgment or review. Size first: a job that reads a lot needs a model whose context fits it.
4. **Starved of context.** An agent starts empty: it does not see the conversation, earlier tool results or loaded skills. Write the body self-contained: inputs it gets ("you will be given X; if missing, do Y"), paths, conventions.
5. **Unusable output.** State exactly what to return and in what shape (a table, a short list, a receipt with paths). A caller should not need to re-read the agent's work.
6. **Delegating coupled work.** Agents pay off for independent parallel work or to keep a large read out of the main context. Tightly coupled, step-by-step work stays inline.

## Rules from the agent's own errors

The "tool errors (calls)" column of "Recurring generic jobs" and the "threads with a tool error" column of "Calls by agent" show work that keeps failing. Find the failing pattern and add one concrete rule to the agent's body, for example: "Write and change files only with Write and Edit, never a shell heredoc."

## File shape

```markdown
---
name: <kebab-name, same as the file name>
description: Use proactively when <situation>. Not for <nearby job> (<where it goes instead>).
tools: Read, Grep, Glob
model: haiku
---

You <one job, one sentence>.

You will be given: <inputs>. If <input> is missing, <what to do>.

Do: <steps, only what this agent needs>.

Return only: <exact shape>.
```

## Check before saving

Write 8–12 short task phrasings, half that should go to this agent and half near-misses that share its words but belong elsewhere. If a should-go phrasing would not match the description, fix the description. If a near-miss would match, add a "Not for …".

## Check after a week

Run the report again. The job should now show under the new agent in "Calls by agent", the general-purpose share should drop, and the agent's error column should stay low. If the agent is not called, the description is the problem.
