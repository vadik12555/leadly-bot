#!/usr/bin/env bash
# Первичная установка Leadly на чистый VPS с Ubuntu 22.04/24.04. Запуск: sudo bash deploy/install.sh
set -euo pipefail
APP_DIR=/opt/leadly
SRC_DIR="$(cd "$(dirname "$0")/.." && pwd)"

apt-get update -y
apt-get install -y python3 python3-venv python3-pip git

id -u leadly >/dev/null 2>&1 || useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin leadly
if [ "$SRC_DIR" != "$APP_DIR" ]; then
  mkdir -p "$APP_DIR"
  cp -a "$SRC_DIR"/. "$APP_DIR"/
fi
cd "$APP_DIR"
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q
mkdir -p data
if [ ! -f .env ]; then
  cp .env.example .env
  echo ">>> Создан $APP_DIR/.env — впиши BOT_TOKEN и ADMIN_IDS: nano $APP_DIR/.env"
fi
chown -R leadly:leadly "$APP_DIR"
chmod 600 .env

cp deploy/leadly.service /etc/systemd/system/leadly.service
systemctl daemon-reload
systemctl enable leadly
echo ">>> Готово. После заполнения .env: sudo systemctl restart leadly && sudo systemctl status leadly"
