# Worker 1 — отчёт (Docker + README)

## Изменённые файлы

| Файл | Суть изменений |
|------|----------------|
| [`docker-compose.yml`](../../docker-compose.yml) | `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` через `${VAR:-closedhub}`; `DATABASE_URL` у `bot` и `web` собран из тех же переменных и `postgres:5432`; healthcheck `pg_isready` с теми же user/db. Том `pgdata`, имена сервисов, путь `./db/schema.sql` → `docker-entrypoint-initdb.d` без изменений. |
| [`README.md`](../../README.md) | Порты: локальный `web.main` → обычно 8000; Compose → хост `WEB_HOST_PORT` (дефолт 8001) → 8000 в контейнере. Новый раздел «Docker Compose и PostgreSQL» (env, дефолты только для локалки, обязательный сильный пароль в проде). Раздел «VPS / Aeza» (TLS, пароль, reverse proxy, ссылки на план и skill). |
## Заметки владельцу

1. **Подстановка переменных** выполняется при `docker compose` на стороне хоста (в т.ч. из `.env` в корне репозитория). Пароль со спецсимволами в `DATABASE_URL` может потребовать URL-encoding — при необходимости дополнить README примером.
2. Ссылки на skill и план см. в корневом `README.md` — skill лежит в [`.cursor/skills/vps-aeza-hosting/SKILL.md`](../../.cursor/skills/vps-aeza-hosting/SKILL.md).
3. **`.env.example` / `config.py` / `web/*`** по заданию воркера 1 не трогались; синхронизация `POSTGRES_*` в примере env сделана воркером 2.
