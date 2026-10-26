---
name: agent-office
description: Delegate a piece of work to a specialist teammate in bojjifomo's Agent Office guild (office team / who / ask). Use when a task has a part another specialist should do, when asked to "ask the team" or "hand this to <name>", or when running as party leader (project manager mode).
---

# Delegating to the guild

The Agent Office keeps a team of specialists, each a Claude Code session in its own folder under `~/Agents/`.
Use the `office` CLI to work with them:

```sh
~/Projects/agent-office/tools/office team                    # who exists: name, title, status, role
~/Projects/agent-office/tools/office who "<task>"            # best fit, highest score first
~/Projects/agent-office/tools/office ask <name> "<brief>"    # send a brief and wait for the answer
~/Projects/agent-office/tools/office ask <name> "<brief>" --no-wait   # returns a session id at once
~/Projects/agent-office/tools/office result <session-id>     # collect the answer (waits if still working)
```

## Writing a brief

Teammates cannot see your conversation. Every brief must stand on its own:

- the goal, and what "done" looks like
- absolute paths of the files or repos involved
- constraints (style, versions, what not to touch)
- the shape of the answer you want back (a list, a patch summary, a yes/no with reasons)

## Habits

- Run `office who` before picking someone unless the user named them.
- Hand independent pieces out together with `--no-wait`, then collect each with `office result`.
- Check what comes back before you build on it, and say in your reply who did what.
- Teammates never ask the user questions; if a piece needs the user's decision, ask it yourself.
