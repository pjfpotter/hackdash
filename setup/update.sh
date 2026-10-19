#!/usr/bin/env bash
# Pull the latest Hackdash and restart it. The TV reloads itself within a few seconds.
set -euo pipefail
cd "$(dirname "$0")/.."
git pull --ff-only
sudo systemctl restart hackdash
echo "Updated. The TV will reload in a few seconds."
