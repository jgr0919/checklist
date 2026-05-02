#!/bin/bash
# Run this on the Lightsail server to deploy updates:
#   bash /home/ubuntu/checklist/deploy/deploy.sh

set -e

APP_DIR="/home/ubuntu/checklist"

echo "=== Pulling latest code ==="
git -C "$APP_DIR" pull origin main

echo "=== Updating dependencies ==="
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -q

echo "=== Restarting app ==="
sudo systemctl restart checklist

echo "=== Done ==="
sudo systemctl status checklist --no-pager
