#!/usr/bin/env bash
# Full-screen Chromium on the TV. Started from ~/.bash_profile on tty1.
DIR="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$DIR/data/config.json"
until [ -f "$CFG" ] && curl -sf "http://localhost:$(python3 -c "import json;print(json.load(open('$CFG'))['port'])")/healthz" >/dev/null; do sleep 1; done
PORT=$(python3 -c "import json;print(json.load(open('$CFG'))['port'])")
KEY=$(python3 -c "import json;print(json.load(open('$CFG'))['key'])")
BROWSER=$(command -v chromium || command -v chromium-browser)
# add &lowfx=1 to the URL if animations stutter; &sound=0 to mute the reward chimes
exec cage -s -- "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
  --disable-features=Translate --check-for-update-interval=31536000 \
  --autoplay-policy=no-user-gesture-required --disk-cache-size=1048576 \
  --ozone-platform=wayland "http://localhost:$PORT/?key=$KEY"
