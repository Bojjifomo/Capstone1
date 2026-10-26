# Agent Office: the Guild Hall

A small web dashboard on the Mac. Every Claude Code session is an adventurer at a quest table,
and you can check on them and give them orders from your phone.

Python 3 standard library only, plain HTML/CSS/JS, no build step, no frameworks.

![The guild town at night](docs/screens/town-night.png)

## Install / update on the Mac

```sh
sh tools/install.sh     # copies to ~/Projects/agent-office, loads com.bojjifomo.agent-office,
                        # puts `office` in ~/.local/bin and the delegation skill in ~/.claude/skills
office doctor           # checks the install
office install-hook     # optional: exact "needs you" when a session waits on a permission prompt
```

Restart: `launchctl kickstart -k gui/$(id -u)/com.bojjifomo.agent-office`

* Mac: `http://127.0.0.1:7777`
* Phone over Tailscale: `http://100.89.176.76:7777`
* Push notifications and the home-screen app need https. Run `tailscale serve --bg --https=443 http://127.0.0.1:7777`
  once, open `https://<your-mac>.<tailnet>.ts.net` on the phone, Share → Add to Home Screen, then 🪶 → *Send ravens to this phone*.
  `install.sh` puts that ts.net name in the Host allowlist for you.

## What's in it

| Part | What it does |
| --- | --- |
| The town | An HD-2D pixel town (drawn in code, no assets): the Guild Hall on the plaza holds your lead sessions, each division is its own building. Agents write at desks, read by the fire, sleep in bed, and walk the road with a scroll when they carry an errand. Day and night follow the clock; tap a building to go inside, tap someone to open them. `☰ List` switches to the plain list |
| The hall | Sessions from `~/.claude/projects` and `ps`: questing (working), awaiting your word (a real question, AskUserQuestion, plan approval or a permission prompt), resting, returned, archived |
| Sheet | Recent conversation as light markdown with copy buttons, a message box, a queue (Send now / Remove), Stop |
| Post a quest | New session in a known project folder, a permission mode, a model, optional party leader (project manager) mode |
| The guild | Recruited specialists in `~/Agents/<name>`, grouped into division wings; `office team / who / ask / result` |
| Petitions | Update proposals from the guild (`office propose`): read the plan, then Approve & build (a quest that ends in a PR, never a merge), Approve for later, or Reject with a note |
| Treasury | Tokens and estimated cost per day, model and session |
| Familiars | Your launchd jobs: running, last run, last error, Run now |
| Vault | Free space, the biggest folders, six fixed cleaners (each needs a phone seal) |
| Scrying | Search across every transcript |
| Ledger | Every action taken from the page: messages, quests, errands, seals, cleaners |
| Ravens & seals | Web push to your phone; one-time 6-digit seals for risky actions |

## Security

* Binds to 127.0.0.1 and the Tailscale IP only (never 0.0.0.0). The Tailscale bind retries until that address exists.
* The Host header must be on the allowlist (stops DNS rebinding). Every POST needs `X-Agent-Office: 1` and a matching `Origin`, or it gets a 403.
* The browser sends ids, never paths or commands. Project folders, cleaners and launchd jobs are looked up server-side, and nothing uses `shell=True`.
* Bypassing permissions and running cleaners need a seal: 6 digits pushed to the phone, stored only as a salted sha256 in memory, valid for 2 minutes, 3 tries, single use, bound to the one action.
* A strict CSP (no inline script or style), and every piece of transcript text is escaped before it becomes HTML.
* Push only goes to known browser push services (Apple, Google, Mozilla, Microsoft).

## State files (next to server.py, written atomically)

`config.json` · `names.json` · `archived.json` · `staff.json` · `queue.json` · `helpers.json` ·
`petitions.json` · `push.json` and `vapid.json` (mode 600) · `ledger.jsonl` (an append-only log)

## Testing a change (never restart the live one while sessions run)

```sh
sh tests/run_copy.sh                         # throwaway copy on :7790 with a fake $HOME and a fake claude
python3 -m unittest discover -s tests        # 41 tests: parsing, status rules, security, runs, team, panels
node tests/shoot.js stage1,stage2,stage4     # phone-width screenshots into docs/screens/
```
