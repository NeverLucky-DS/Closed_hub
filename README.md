# Closed Hub

**LLM-платформа** для закрытого ML-сообщества: Telegram собирает события и материалы, **Mistral** маршрутизирует intent, саммаризирует ленту и извлекает HR-контекст. **pgvector** для embeddings. FastAPI-веб показывает те же данные из PostgreSQL.

**Live demo:** [hub-ml.ru](https://hub-ml.ru)

**Стек:** Mistral · pgvector · PostgreSQL · FastAPI · Telegram · Docker · pytest (22)

**Поток:** Telegram → routing (heuristic + LLM) → services → PostgreSQL → веб

## Демонстрация

### Библиотека файлов

Тематические папки, drag-and-drop загрузка PDF, разделение «Общее» / «Личное».

![Библиотека — файлы по темам](screenshots/CH_1.png)

Поиск по названию, описанию и теме; карточки с категорией и автором.

![Библиотека — поиск «AB-тесты»](screenshots/CH_2.png)

### Полезные ссылки

Темы, недавние ресурсы, добавление URL с автозаполнением через LLM (~15 сек).

![Ресурсы — каталог ссылок](screenshots/CH_3.png)

### Лента и выжимка

AI-саммари мероприятий из Telegram-бота; дедлайны, фильтр по актуальности.

![Лента новостей хаба](screenshots/CH_4.png)

![Выжимка — дедлайны на сегодня, неделю и позже](screenshots/CH_5.png)

### Профиль и AI-анализ

Приватный разбор PDF-резюме и публичного GitHub без OAuth.

![AI-резюме чек](screenshots/CH_6.png)

### QA-панель (web-admin)

Запуск pytest на сервере, health/ready, unit + integration с отдельной БД `closedhub_test`.

![Панель тестов — статус сервисов и прогон](screenshots/CH_7.png)

![Integration-тесты: PostgreSQL, routing, LLM → DB](screenshots/CH_8.png)

**Live:** [hub-ml.ru](https://hub-ml.ru)

---

## Быстрый старт

```bash
cp .env.example .env   # TELEGRAM_BOT_TOKEN, MISTRAL_API_KEY, WEB_SESSION_SECRET
docker compose up -d   # Postgres :5433, web http://localhost:8001
```

Локально без Docker:

```bash
uv sync
uv run python -m bot.main    # Telegram-бот
uv run python -m web.main    # веб на :8000
```

Переменные — в [`.env.example`](.env.example).

---

## Архитектура

```
Telegram → bot/handlers → routing (heuristic + LLM) → services → PostgreSQL
                              ↓
                         prompts/ + Mistral API
                              ↓
FastAPI web ← asyncpg pool ← те же таблицы (лента, компании, файлы)
```

| Путь | Назначение |
|------|------------|
| `bot/` | Telegram bot, handlers |
| `web/` | FastAPI, templates, static |
| `db/` | asyncpg pool, repo, schema.sql |
| `services/` | LLM, routing, events, files, HR, search |
| `prompts/` | промпты Mistral (отдельно от кода) |
| `docs/changelog/` | история изменений |

---

## Тесты

```bash
uv sync
uv run pytest -v
```

Покрыто: agent router (`heuristic_route`, `route_intent` с mock LLM), парсинг HR-контактов, `GET /health` и `GET /ready`. Внешние API (Mistral, Telegram) не вызываются.

---

## Production / VPS

- Deploy: [`deploy/README.md`](deploy/README.md)
- Health: `GET /health`, `GET /ready`

Секреты не коммитить: `.env`, `storage/`.
