#!/usr/bin/env bash
# Hackdash installer for Raspberry Pi OS Lite (Bookworm or newer), Pi Zero 2 W.
# Run as your normal user from the hackdash folder:   ./setup/install.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
ME="$(whoami)"

echo ">> packages: cage (tiny kiosk compositor) + chromium"
sudo apt-get update
sudo apt-get install -y --no-install-recommends cage curl python3 \
  && (sudo apt-get install -y --no-install-recommends chromium || sudo apt-get install -y --no-install-recommends chromium-browser)

echo ">> hackdash server as a service"
sudo tee /etc/systemd/system/hackdash.service >/dev/null <<EOF
[Unit]
Description=Hackdash server
After=network-online.target
Wants=network-online.target

[Service]
User=$ME
WorkingDirectory=$DIR
ExecStart=/usr/bin/python3 $DIR/server.py
Restart=always
RestartSec=3
# Optional live bank feed: any script that prints a balance (see README)
#Environment=HACKDASH_FINANCE_CMD=$DIR/bank/fetch_balance.py
#Environment=HACKDASH_FINANCE_EVERY=30

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable --now hackdash

echo ">> boot straight into the dashboard (console autologin -> kiosk on tty1)"
sudo raspi-config nonint do_boot_behaviour B2
chmod +x "$DIR/setup/kiosk.sh"
if ! grep -q hackdash-kiosk "$HOME/.bash_profile" 2>/dev/null; then
  cat >> "$HOME/.bash_profile" <<EOF

# hackdash-kiosk: start the dashboard on the TV console only
if [ "\$(tty)" = "/dev/tty1" ]; then exec "$DIR/setup/kiosk.sh"; fi
EOF
fi

echo ">> more swap so Chromium is happy in 512MB"
if [ -f /etc/dphys-swapfile ]; then
  sudo sed -i 's/^CONF_SWAPSIZE=.*/CONF_SWAPSIZE=512/' /etc/dphys-swapfile
  sudo systemctl restart dphys-swapfile || true
fi

# A 1080p TV makes the Zero 2 W work 2.25x harder for no visible gain on this design.
CMDLINE=/boot/firmware/cmdline.txt
if [ "${HACKDASH_720P:-1}" = "1" ] && [ -f "$CMDLINE" ] && ! grep -q "video=HDMI-A-1" "$CMDLINE"; then
  echo ">> forcing HDMI to 1280x720 (set HACKDASH_720P=0 to skip)"
  sudo sed -i '1 s/$/ video=HDMI-A-1:1280x720@60/' "$CMDLINE"
fi

sleep 2
PORT=$(python3 -c "import json;print(json.load(open('$DIR/data/config.json'))['port'])")
KEY=$(python3 -c "import json;print(json.load(open('$DIR/data/config.json'))['key'])")
IP=$(hostname -I | awk '{print $1}')
cat <<EOF

  DONE. Reboot to start the kiosk:   sudo reboot

  Phone remote (same wifi):  http://$IP:$PORT/remote?key=$KEY
  Next: ./setup/remote-access.sh  so Claude (and your phone, anywhere) can reach it.
EOF
