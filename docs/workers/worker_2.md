# Worker 2 — security / env defaults

## Summary

- **`config.py`:** `web_admin_telegram_ids` default is now `""` instead of a hardcoded Telegram id, so deployers who omit `.env` do not grant site admin to an unrelated account. `web_admin_id_set` already returns `frozenset()` for an empty raw string.
- **`.env.example`:** `WEB_ADMIN_TELEGRAM_IDS` comments state that empty disables all web admins and that production should list explicit numeric ids; the example line no longer contains a real-looking id (placeholder pattern in text only).
- **`.env.example`:** Added `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB` with names aligned to `docker-compose.yml` (postgres service `environment` keys). Values match the current compose defaults (`closedhub`).
- **`.env.example`:** Added `WEB_UVICORN_WORKERS=1` with a note that in-memory throttling in `web/app.py` is per process; keep `1` until a shared limiter (e.g. Redis) exists.
- **`.env.example`:** Tightened `DATABASE_URL` / Postgres comments so host URL uses `POSTGRES_HOST_PORT` and the same user/password/DB as `POSTGRES_*`.
- **`docker-compose.yml`:** not modified (another worker); read only for naming alignment.
