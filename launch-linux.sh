#!/usr/bin/env bash
set -e

export PATH="$HOME/.local/bin:$HOME/.local/opt/node/bin:$HOME/.local/go/bin:$HOME/bin:$PATH"
touch "$HOME/.Xauthority" 2>/dev/null || true
pactl set-source-volume RDPSource 100% 2>/dev/null || true

cd /home/tanishq/Mark-LVII
exec /home/tanishq/Mark-LVII/.venv/bin/python /home/tanishq/Mark-LVII/main.py > /home/tanishq/Mark-LVII/jarvis-linux.log 2>&1
