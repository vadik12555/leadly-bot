#!/usr/bin/env bash
# Обновление кода из Git и перезапуск. Запуск: sudo bash /opt/leadly/deploy/update.sh
set -euo pipefail
cd /opt/leadly
cp data/leadly.db "data/leadly.db.bak-$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true
sudo -u leadly git pull --ff-only
sudo -u leadly .venv/bin/pip install -r requirements.txt -q
systemctl restart leadly
sleep 3
systemctl --no-pager status leadly | head -5
