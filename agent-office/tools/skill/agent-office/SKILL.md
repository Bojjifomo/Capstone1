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

## Proposing an update (petitions)

Changes to bojjifomo's existing apps are not made on your own say-so. Write the plan, then send it as a petition;
nothing is built until they approve it from the Guild Hall on their phone:

```sh
~/Projects/agent-office/tools/office propose --title "<short title>" --app "<which app>" \
    --summary "<one sentence: what and why>" --impact high|medium|low --effort S|M|L < plan.md
~/Projects/agent-office/tools/office petitions     # what was approved, rejected (with their note) or is being built
```

A good plan says: the problem, the change (files and modules it touches), how it will be tested, the risks
(RPC cost, keys, data that could be wrong) and how to roll it back. When a petition is approved and built, a new
session gets the plan and opens a pull request; it never merges. A near-duplicate title for the same app is
refused while one is still open. Pending petitions expire after `petition_expire_days` (default 14).

Quill, in the Counting House, writes a read-only LP brief: `office brief`. That path never runs write tools.
Do not hand Quill a normal coding errand.

## The chain of command

bojjifomo's programs are **interns**, each looked after by a **warden**. The **Operations Manager** makes rounds,
the **CEO** is the backup, the **Innovator** files petitions. If your brief came from the chain (an incident or a
round), use:

```sh
~/Projects/agent-office/tools/office chain                     # every intern, its health, open incidents
~/Projects/agent-office/tools/office check "<intern>"          # check one now; exit 1 while it is down
~/Projects/agent-office/tools/office incident <id> resolve "<what fixed it>"
~/Projects/agent-office/tools/office incident <id> escalate "<why it needs someone higher>"
```

Fix on a branch with a pull request, never merge to main, never touch secrets or wallets. Confirm with
`office check` before you say it is fixed.

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
