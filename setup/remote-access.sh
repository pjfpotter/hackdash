#!/usr/bin/env bash
# Put the Pi on the internet over HTTPS with Tailscale Funnel, so Claude (claude.ai
# custom connector) and your phone can reach it from anywhere. Free for personal use.
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$DIR/data/config.json"

command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up            # prints a login link the first time: open it on your phone
PORT=$(python3 -c "import json;print(json.load(open('$CFG'))['port'])")
# The first run may print a link to enable Funnel for your tailnet. Open it, then re-run this script.
sudo tailscale funnel --bg "$PORT"

HOST=$(tailscale status --json | python3 -c "import json,sys;print(json.load(sys.stdin)['Self']['DNSName'].rstrip('.'))")
KEY=$(python3 -c "import json;print(json.load(open('$CFG'))['key'])")
TOKEN=$(python3 -c "import json;print(json.load(open('$CFG'))['mcp_token'])")
cat <<EOF

  Claude connector URL (claude.ai > Settings > Connectors > Add custom connector):
    https://$HOST/mcp/$TOKEN

  Phone remote (bookmark it / add to home screen):
    https://$HOST/remote?key=$KEY

  Keep both secret: anyone with them can read and change your dash.
  To rotate: delete $CFG, then: sudo systemctl restart hackdash
EOF
