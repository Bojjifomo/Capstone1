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
* An external assistant on the public internet is a different door. See [External access](#external-access-tailscale-funnel). Funnel and Serve cannot share a port, so leave Serve on 443 for the phone.

## What's in it

| Part | What it does |
| --- | --- |
| The town | An HD-2D pixel town (drawn in code, no assets): the Guild Hall on the plaza holds your lead sessions, each division is its own building. Agents write at desks, read by the fire, sleep in bed, and walk the road with a scroll when they carry an errand. Day and night follow the clock; tap a building to go inside, tap someone to open them. `☰ List` switches to the plain list |
| Life in town | Every agent has a fantasy persona (name, kin and calling, age, quirk, home town, bio, motto). Idle agents walk to **the Tavern** for a break and back to their desk when work starts; they chatter in speech bubbles ("Who broke main?" / "Not me!"). Sessions can be moved into a division's building. Tapping a building shows its roster |
| Profile card | Top of every sheet: persona plus Started as, Division, Project, Branch, Model, Pay today / 7 days / 30 days, Last activity, Last tool, Folder. **✨ Summarize with Haiku** and **✨ Suggest replies** run one-shot Haiku calls (no tools, nothing saved) |
| The hall | Sessions from `~/.claude/projects` and `ps`: questing (working), awaiting your word (a real question, AskUserQuestion, plan approval or a permission prompt), resting, returned, archived |
| Sheet | Recent conversation as light markdown with copy buttons, a message box, a queue (Send now / Remove), Stop |
| Post a quest | New session in a known project folder, a permission mode, a model, optional party leader (project manager) mode |
| The guild | Recruited specialists in `~/Agents/<name>`, grouped into division wings; `office team / who / ask / result` |
| Petitions | Update proposals from the guild (`office propose`): read the plan, then Approve & build (a quest that ends in a PR, never a merge), Approve for later, or Reject with a note |
| Treasury | Tokens and estimated cost per day, model and session |
| Familiars | Your launchd jobs: running, last run, last error, Run now |
| Vault | Free space, the biggest folders, six fixed cleaners (each needs a phone seal) |
| Scrying | Search across every transcript |
| Ledger | Every action taken from the page, and every token call from outside: messages, quests, errands, seals, cleaners |
| Ravens & seals | Web push to your phone; one-time 6-digit seals for risky actions |

## Security

* Binds to 127.0.0.1 and the Tailscale IP only (never 0.0.0.0). The Tailscale bind retries until that address exists.
* The Host header must be on the allowlist (stops DNS rebinding). Every POST needs `X-Agent-Office: 1` and a matching `Origin`, or it gets a 403. A request with no `Authorization` header still follows exactly these rules.
* Public Funnel traffic and names in `external_hosts` need `Authorization: Bearer <token>`. The token only opens the route list in the next section. A wrong token is 401. Seals still apply to risky quest modes.
* The browser sends ids, never paths or commands. Project folders, cleaners and launchd jobs are looked up server-side, and nothing uses `shell=True`.
* Bypassing permissions and running cleaners need a seal: 6 digits pushed to the phone, stored only as a salted sha256 in memory, valid for 2 minutes, 3 tries, single use, bound to the one action.
* A strict CSP (no inline script or style), and every piece of transcript text is escaped before it becomes HTML.
* Push only goes to known browser push services (Apple, Google, Mozilla, Microsoft).

## External access (Tailscale Funnel)

The phone stays on Tailscale Serve, inside the tailnet. To let Guild Hall (the assistant on another machine) call this dashboard over the public internet, give the server a secret and turn on Funnel. Local pages and tailnet requests do not change.

Tailscale sends public Funnel requests to the same MagicDNS name as Serve, and marks them with `Tailscale-Funnel-Request: ?1`. It sets that header itself and strips any copy the caller sent, so it cannot be forged through the proxy. Those requests need the bearer token even though `install.sh` already put the name in `extra_hosts` for the phone. A Serve request to that name has no such header and keeps working as before.

1. Choose a secret. The environment wins over the file, so a launchd plist can hold it without a second copy in git (`config.json` is gitignored either way):

   ```sh
   # config.json, next to server.py — leave this blank if the environment is set
   "external_token": ""
   ```

   `AGENT_OFFICE_TOKEN` in the server's environment overrides `external_token`. Never commit a real token. Restart the agent after changing it (`launchctl kickstart -k gui/$(id -u)/com.bojjifomo.agent-office`). `install.sh` rewrites the plist, so put the variable back if you keep it there; a value in `config.json` survives install.

2. Optional: list extra Funnel names in `external_hosts`. A name in that list needs the token on every request, header or not, so do not put the phone's MagicDNS name there if the phone should keep opening the page without a token. The Funnel header already covers that name.

   `external_origin` (default `grokbot`) is the marker written on ledger lines for token calls.

3. Turn on Funnel in the background. This is the current Tailscale command (client 1.52 and later; checked against the Funnel docs). It proxies local port 7777 and publishes HTTPS on port 443:

   ```sh
   tailscale funnel --bg 7777
   tailscale funnel status          # prints https://<mac>.<tailnet>.ts.net
   tailscale funnel reset           # turn it off
   ```

   `tailscale funnel 7777` without `--bg` does the same until you stop it. Funnel on macOS needs an open-source Tailscale build, not the Mac App Store app. The last of Serve or Funnel to claim a port wins, and Funnel makes that port public, so do not point Funnel at the port Serve is using for the phone. The tailnet URL `http://100.x.x.x:7777` keeps working either way.

4. From the other machine:

   ```sh
   curl -sS "https://<mac>.<tailnet>.ts.net/api/state" \
     -H "Authorization: Bearer $AGENT_OFFICE_TOKEN"

   curl -sS -X POST "https://<mac>.<tailnet>.ts.net/api/ledger" \
     -H "Authorization: Bearer $AGENT_OFFICE_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"kind":"chatter","text":"The watch reports all quiet.","by":"Guild Hall"}'
   ```

   `office` sends the same header when `AGENT_OFFICE_TOKEN` is set and `AGENT_OFFICE_URL` is a remote https URL. It does not send the token to `127.0.0.1`, `localhost`, or a Tailscale CGNAT address (`100.64.0.0/10`), so the CLI on the Mac stays on the old rules. Remote `office` can only call the routes below. `office result` and session messages stay on the Mac.

### What a token may call

| | |
| --- | --- |
| Read | `GET /api/state`, `/api/petitions`, `/api/team`, `/api/team/who`, `/api/ledger`, `/api/usage`, `/api/bots`, `/api/orchestrator` (Quill's last brief) |
| Write | `POST /api/petitions` (new petition), `POST /api/petitions/<id>/decide` (approve or reject, with a note), `POST /api/team/ask` (not Quill), `POST /api/job` (a new quest, including `"pm": true`), `POST /api/ledger` (`kind` `guild` or `chatter`, `text`, `by`) |

Anything else, including session send, hiring, launchd, cleaners, seals, push, starting Quill's brief (`POST /api/orchestrator/brief` or `ask` of Quill), and the static page, is 403. `decide` with `"build": true` is 403 as well; building a petition stays on the phone. A risky quest mode still needs a phone seal.

Every accepted token call is a ledger line `kind: external` with `origin` set (default `grokbot`), and that same origin is stamped on the action itself (the quest, the petition, the errand). The town chat has an **External** tab for these lines; `chatter` also shows under Chatter and `guild` under Guild.

A missing or wrong token on a Funnel request or an `external_hosts` name is 401. The failure is written to the server log without the token. After 8 failures from the same client within 10 minutes the answer is 429. Funnel connects from 127.0.0.1 and sets `X-Forwarded-For` to the public client; that address is what the limit counts. A correct token clears the count. A wrong token on localhost is also 401, so a mismatched `AGENT_OFFICE_TOKEN` fails closed. A correct token on localhost does not open the allowlist and does not skip the Origin check.

## Webhooks

`config.json` holds a list. Each entry is a URL and an optional secret. Delivery is a background thread with a short timeout (`webhook_timeout`, default 5 seconds). A failure is one line on stderr and does not affect the page.

```json
"webhooks": [
  {"url": "https://example.com/guild", "secret": "choose-one", "header": "X-Guild-Token"}
]
```

Leave `header` out to send only the signature. When `secret` is set the body is signed as `X-Guild-Signature: sha256=<hex hmac-sha256 of the raw body>`. If `header` is also set, that header's value is the secret itself. `events` is an optional list; omit it to receive every event.

```json
{"event": "petition.added", "ts": 1710000000.0, "source": "agent-office", "data": {}}
```

| Event | When |
| --- | --- |
| `petition.added` | A petition was filed. `data` is the petition without the plan |
| `petition.decided` | Approved, rejected, or expired (`data.decision`) |
| `petition.closed` | Status became `merged`, `closed`, or `failed` (`data.pr_url` when there is one) |
| `build.started` | The build quest for an approved petition started |
| `quest.finished` | A Claude run ended (`sid`, `ok`, `cost`, `origin`) |
| `familiar.error` | A launchd job showed a new error line |

Pending petitions older than `petition_expire_days` (default 14, `0` turns it off) expire. A new petition whose title is a near-duplicate of an open one for the same app is refused. The hall still keeps at most 200 petitions and drops settled ones first.

After a build quest finishes, a GitHub pull-request URL in its report is stored on the petition. Every `pr_poll_seconds` (default 300, `0` turns it off) the hall asks `gh pr view` when `gh` is on the PATH, otherwise the public GitHub API. `merged` and `closed` update the petition; if GitHub cannot be reached the status stays `building`. A quest that fails before any PR link is `failed`.

## The Counting House

Quill the Treasurer is always on the roster, stationed in the Counting House (breaks at the Tavern, like any idle adventurer). *Write today's brief* or `office brief` runs `python3 -m orchestrator brief` with `ORCHESTRATOR_READ_ONLY=1` and never `--yes`, so write tools are not offered and cannot be confirmed. The report is kept in `orchestrator.json` and a short line is written to the guild log. Set `orchestrator_root` if this install is not sitting next to the `orchestrator` package. Token callers may read `GET /api/orchestrator` and may not start a brief.

## State files (next to server.py, written atomically)

`config.json` · `names.json` · `archived.json` · `staff.json` · `queue.json` · `helpers.json` ·
`petitions.json` · `assignments.json` · `orchestrator.json` · `push.json` and `vapid.json` (mode 600) · `ledger.jsonl` (an append-only log)

## Testing a change (never restart the live one while sessions run)

```sh
sh tests/run_copy.sh                         # throwaway copy on :7790 with a fake $HOME and a fake claude
python3 -m unittest discover -s tests        # 56 tests: parsing, status rules, security, runs, team, panels, external token
node tests/shoot.js stage1,stage2,stage4     # phone-width screenshots into docs/screens/
```
