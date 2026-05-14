# Worker 4 — HTTP health endpoints

## Что сделано

- Пакет `web/routers/`: роутер `health.py` с префиксом `""` (маршруты на корне приложения).
- **`GET /health`** — лёгкая проверка процесса: JSON `{"status": "ok"}`, без БД и пула.
- **`GET /ready`** — проверка готовности к трафику: из `request.app.state.pool` берётся соединение, выполняется `SELECT 1`. Успех → `{"ready": true}`. Ошибка (нет пула, asyncpg, сеть к БД) → **503** и компактный JSON `{"ready": false}`.
- В `web/app.py` сразу после `app = FastAPI(...)` вызывается `app.include_router(health_router)`, до `SessionMiddleware` и HTTP-middleware, чтобы маршруты зарегистрировались без дублирования существующих путей.

Пул по-прежнему создаётся в `lifespan` (`app.state.pool`).

## Как проверить (curl)

Подставь хост и порт своего uvicorn (по умолчанию часто `127.0.0.1:8000`):

```bash
curl -sS -i http://127.0.0.1:8000/health
curl -sS -i http://127.0.0.1:8000/ready
```

Ожидания:

- `/health` — **200**, тело `{"status":"ok"}`.
- `/ready` при живой БД — **200**, тело `{"ready":true}`; при недоступной БД или до инициализации пула — **503**, тело `{"ready":false}`.

Для только тела без заголовков:

```bash
curl -sS http://127.0.0.1:8000/health
curl -sS http://127.0.0.1:8000/ready
```
