#!/usr/bin/env bash
# Быстро пересобирает локальный web/bot без обращения к Docker Hub, если базовые образы уже есть.
set -euo pipefail

if [[ "${1:-}" == "--full" ]]; then
  shift
  docker compose build bot web
else
  if docker image inspect closed_hub-bot:latest closed_hub-web:latest >/dev/null 2>&1; then
    docker compose -f docker-compose.yml -f docker-compose.local.yml build bot web
  else
    echo "Локальных образов closed_hub-bot/web пока нет. Запускаю полную сборку."
    docker compose build bot web
  fi
fi

docker compose up -d "$@"
