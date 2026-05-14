# change_83 — подготовка к VPS (Aeza): секреты, health, CI, воркеры

**Что сделано**

- **Docker Compose:** `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` из окружения с дефолтами для локалки; `DATABASE_URL` у `bot`/`web` собран из тех же переменных; healthcheck `pg_isready` согласован с пользователем и именем БД.
- **README:** порты (8000 локально vs `WEB_HOST_PORT` в compose), блок про Postgres env, раздел VPS/Aeza со ссылками на план и skill.
- **`config.py`:** дефолт `web_admin_telegram_ids` пустой — без случайного чужого админа.
- **`.env.example`:** `POSTGRES_*`, `WEB_UVICORN_WORKERS`, уточнения по `WEB_ADMIN_TELEGRAM_IDS` и `DATABASE_URL`.
- **`web/routers/health.py`:** `GET /health`, `GET /ready` (проверка пула БД).
- **`web/main.py`:** `WEB_UVICORN_WORKERS` (по умолчанию 1) и предупреждение при workers>1 про in-memory rate limits.
- **CI:** `.github/workflows/ci.yml` — `uv sync --frozen` и дымовой импорт `web.app` с плейсхолдер-переменными.

**Зачем:** безопасные дефолты для публичного репозитория, наблюдаемость для прокси/оркестраторов, воспроизводимая проверка в CI, явная политика одного воркера без Redis.
