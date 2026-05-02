#!/usr/bin/env bash
# Печатает адрес веб-хаба для браузера на телефоне (та же Wi‑Fi сеть, что и этот Mac).
set -euo pipefail
PORT="${WEB_PORT:-8000}"
IFACE=""
if IFACE=$(route -n get default 2>/dev/null | awk '/interface: / { print $2 }'); then
  :
fi
IP=""
if [[ -n "${IFACE:-}" ]]; then
  IP=$(ipconfig getifaddr "$IFACE" 2>/dev/null || true)
fi
if [[ -z "$IP" ]]; then
  IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)
fi
if [[ -z "$IP" ]] && command -v ip >/dev/null 2>&1; then
  IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") print $(i+1); exit}' || true)
fi
if [[ -z "$IP" ]]; then
  IP=$(ifconfig 2>/dev/null | grep "inet " | grep -v "127.0.0.1" | head -1 | awk '{ print $2 }' || true)
fi
if [[ -z "$IP" ]]; then
  echo "Не удалось определить IP. Проверь Wi‑Fi или открой «Системные настройки» → «Сеть» и посмотри IP вручную."
  exit 1
fi
echo "На телефоне открой (та же Wi‑Fi, что и Mac):"
echo "  http://${IP}:${PORT}/"
echo ""
echo "На этом Mac:"
echo "  http://127.0.0.1:${PORT}/"
echo ""
echo "Если страница не открывается: macOS → Системные настройки → Сеть → Брандмауэр — разреши входящие для Python/uvicorn или временно отключи для проверки."
