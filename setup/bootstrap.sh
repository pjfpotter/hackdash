#!/usr/bin/env bash
# One-liner install on a fresh Pi:
#   curl -fsSL https://raw.githubusercontent.com/pjfpotter/hackdash/main/setup/bootstrap.sh | bash
set -euo pipefail
command -v git >/dev/null || { sudo apt-get update && sudo apt-get install -y git; }
if [ -d "$HOME/hackdash/.git" ]; then
  git -C "$HOME/hackdash" pull --ff-only
else
  git clone https://github.com/pjfpotter/hackdash.git "$HOME/hackdash"
fi
cd "$HOME/hackdash"
chmod +x setup/*.sh server.py
./setup/install.sh
