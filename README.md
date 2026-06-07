# Closed Hub

Backend-платформа закрытого сообщества: **Telegram-бот** + **FastAPI веб** + **PostgreSQL** + **Mistral LLM** (маршрутизация intent, саммари, классификация) + **async workers**.

Self-hosted: Docker Compose, `.env`, без привязки к конкретному контенту.

---

## Зачем этот проект (backend / AI)

Главный репозиторий для демонстрации опыта backend-разработки агентских модулей.

| Навык | Реализация |
|-------|------------|
| Python | bot, web, 20+ services |
| FastAPI | REST + HTML (Jinja2), `/health`, `/ready` |
| PostgreSQL | asyncpg, pgvector, `db/repo.py`, schema patches |
| Тесты | pytest (в разработке), CI smoke import |
| LLM / агенты | `services/routing.py` → `services/llm.py`, промпты в `prompts/` |
| Async | asyncpg, httpx, `asyncio.Queue` worker |
| Git, Docker | docker-compose, GitHub Actions, changelog |

**Поток данных:** Telegram → handlers → routing (heuristic + LLM) → services → PostgreSQL; веб читает те же данные.

---

## Скриншоты

```markdown
![Лента](docs/screenshots/feed.png)
```

| Файл | Что снять |
|------|-----------|
| `feed.png` | веб-лента / главная |
| `bot.png` | диалог с ботом в Telegram |
| `architecture.png` | схема bot → services → LLM → DB (draw.io / excalidraw) |

Папка: `docs/screenshots/`. PNG/JPG в commit — GitHub отрисует в README.

---

## Быстрый старт

1. `cp .env.example .env` — `TELEGRAM_BOT_TOKEN`, `MISTRAL_API_KEY`, `WEB_SESSION_SECRET`, Postgres.
2. `docker compose up -d` — Postgres + bot + web (с хоста веб: `http://localhost:8001` по умолчанию).
3. Локально: `uv sync`, `uv run python -m bot.main` и `uv run python -m web.main`.

Переменные — в [`.env.example`](.env.example).

---

## Структура

| Путь | Назначение |
|------|------------|
| `bot/` | Telegram bot, handlers |
| `web/` | FastAPI, templates, static |
| `db/` | asyncpg pool, repo, schema.sql |
| `services/` | LLM, routing, events, files, HR, search |
| `prompts/` | промпты Mistral (отдельно от кода) |
| `docs/changelog/` | история изменений |

---

## Production / VPS

- Deploy: [`deploy/README.md`](deploy/README.md)
- Health: `GET /health`, `GET /ready`
- Планы: [`docs/plans/vps_aeza_hosting_plan.md`](docs/plans/vps_aeza_hosting_plan.md)

Секреты не коммитить: `.env`, `storage/`.
