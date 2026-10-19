# HACKDASH

A life dashboard for an old TV, in the style of an 80s/90s hacker-movie terminal. It runs on a Raspberry Pi Zero 2 W and needs no keyboard. You control it from your phone or by telling Claude to "update the dash".

**On screen:** today's todos, weekly goals, live projects with milestones and progress (with a WIP limit), bank balance with a sparkline and budget bar, Claude plan usage, and the **TA-DA list**. Every finished thing fires a full-screen celebration with XP, a level-up and a rank (SCRIPT KIDDIE up to MAINFRAME GOD). Boring chores are worth double XP.

No dependencies: Python 3 stdlib plus Chromium.

```
server.py      HTTP server: TV page, phone remote, JSON API, live updates (SSE), MCP endpoint
store.py       all state + rules (XP, WIP limit, carry-over), saved to data/state.json
commands.py    plain-text commands for the phone (dictation friendly)
mcp.py         MCP tools so Claude can read and update the dash
static/        index.html (TV), remote.html (phone)
setup/         install.sh, kiosk.sh, remote-access.sh
```

## Milestone 1: on the TV (about an evening)

1. Flash **Raspberry Pi OS Lite (64-bit)** with Raspberry Pi Imager. In the settings cog, set wifi, a username, and enable SSH.
2. Copy this folder to the Pi: `scp -r hackdash pi@<pi>.local:~`
3. On the Pi: `cd ~/hackdash && ./setup/install.sh && sudo reboot`

The TV boots straight into the dashboard. The installer prints the phone remote link.

## Milestone 2: Claude can update it

1. `./setup/remote-access.sh` (Tailscale Funnel gives the Pi a private HTTPS address)
2. claude.ai → Settings → Connectors → **Add custom connector** → paste the `/mcp/...` URL it prints.
3. In the Claude app on your phone, turn the connector on in a chat, brain-dump, and say **"update the dash"**.

Claude gets instructions with the tools: keep todos short, mark chores as boring, log ta-das for things already done, park new project ideas when you're at the WIP limit, and point you at the project closest to its next milestone.

## The phone remote (Wispr Flow works well)

Open the remote link and add it to your home screen. Tap the box, dictate, then press SEND. One command per line:

```
todo call the dentist, buy milk      boring do the tax return (2x XP)
done milk                            tada cleaned the oven
goal ship hackdash v1                goal gym 66%
project hackdash 40% next: buy a mini-HDMI adapter
project novel parked                 milestone hackdash: on the TV
balance 2431.07                      tokens 38 71   (session% weekly%)
tokens weekly 71 resets 2d5h         theme amber    (cyan / green / amber)
privacy on                           say back at 6
```

Anything that doesn't start with a command becomes a todo.

## Things to know

- **Claude plan usage has no API.** Read the percentages from claude.ai → Settings → Usage and tell Claude or the remote (`tokens 38 71`). The panel shows when the data is stale.
- **Live bank balance:** set `HACKDASH_FINANCE_CMD` in the service file to any script that prints a number or `{"balance": 123.45, "accounts": [...]}`. It runs every 30 minutes. Which script depends on your bank (see below). Until then, Claude or `balance 1234.56` sets it.
- **Night mode** dims the screen from 23:00 to 07:00. The layout shifts a few pixels every 10 minutes to protect an old panel.
- **If it stutters:** add `&lowfx=1` to the URL in `setup/kiosk.sh`. Add `&sound=0` to mute the reward chimes.
- **Security:** the API needs the key and the MCP URL contains a secret token. Treat both like passwords. Delete `data/config.json` and restart to rotate them.
- **Updating:** `~/hackdash/setup/update.sh` pulls the latest version and restarts; the TV reloads itself.
- **Run it on a laptop first:** `python3 server.py` and open the TV link it prints.

## Later (not v1)

- Bank feed script for your bank (Starling and Monzo have personal tokens; other UK/EU banks via an open-banking aggregator; US via SimpleFIN)
- HDMI-CEC to switch the TV off at night
- Google Calendar strip
