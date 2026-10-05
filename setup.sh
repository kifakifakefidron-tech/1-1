#!/usr/bin/env bash
# Первичная подготовка чистого сервера Ubuntu. Запуск: bash setup.sh
# Можно запускать повторно — уже сделанное не ломает.
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Ставлю Docker"
  curl -fsSL https://get.docker.com | sh
fi

mkdir -p data
chown 1000:1000 data

if [ ! -f .env ]; then
  echo "==> Создаю .env"
  cp .env.example .env
  ip=$(hostname -I | awk '{print $1}')
  sed -i "s|^SECRET_KEY=.*|SECRET_KEY=$(openssl rand -hex 32)|" .env
  sed -i "s|^SITE_DOMAIN=.*|SITE_DOMAIN=${ip//./-}.sslip.io|" .env
fi

if command -v ufw >/dev/null 2>&1; then
  ufw allow OpenSSH >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
  ufw --force enable >/dev/null
fi

echo
echo "Готово. Адрес сайта будет: https://$(grep '^SITE_DOMAIN=' .env | cut -d= -f2)"
echo "Дальше: nano .env  (вписать ключи), затем: docker compose up -d --build"
