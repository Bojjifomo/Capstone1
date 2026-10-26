# Agent Office: the Guild Hall

A small web dashboard for this Mac. Every Claude Code session is an adventurer at a quest table,
and you can check on them and talk to them from your phone.

Python 3 standard library only, plain HTML/CSS/JS, no build step.

## Install / update on the Mac

```sh
sh tools/install.sh          # copies to ~/Projects/agent-office and loads com.bojjifomo.agent-office
launchctl kickstart -k gui/$(id -u)/com.bojjifomo.agent-office   # restart
```

Open `http://127.0.0.1:7777` on the Mac, or `http://100.89.176.76:7777` from the phone over Tailscale.

## Testing a change (never restart the live one while sessions run)

```sh
sh tests/run_copy.sh                     # throwaway copy on :7790 with a fake $HOME
python3 -m unittest discover -s tests    # unit + security tests (uses :7791)
node tests/shoot.js stage1 7790          # phone-width screenshots into docs/screens/
```
