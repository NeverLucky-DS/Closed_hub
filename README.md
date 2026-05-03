# Closed Hub

Закрытая **инфраструктура** для небольшого сообщества: один **Telegram-бот** (личные сообщения + форум-группа с темами), **веб-интерфейс** на том же PostgreSQL и набор **сервисов** (маршрутизация контента, ИИ, файлы, синхронизация с таблицами). Репозиторий рассчитан на развёртывание у себя: Docker Compose, явные переменные окружения, без привязки к конкретному бренду или контенту.

## Быстрый старт

1. **Клонировать** репозиторий, установить [uv](https://github.com/astral-sh/uv) и (по желанию) Docker.
2. **Окружение:** `cp .env.example .env` и заполнить минимум `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `MISTRAL_API_KEY`, `WEB_SESSION_SECRET`, `DATABASE_URL` (или полагаться на значения из примера для локального Postgres).
3. **База и процессы:**
   - `docker compose up -d` — поднимет Postgres, бота и веб на порту `8000` (см. `docker-compose.yml`).
   - Локально без контейнера бота: `uv sync`, затем `uv run python -m bot.main` и отдельно `uv run python -m web.main`.

Подробные переменные — в [`.env.example`](.env.example).

## Как пользоваться

### Участник в Telegram

- Написать боту в **личку**: обработка текста, вложений, голосовых (при наличии `GROQ_API_KEY`), сценарии из [`bot/handlers/messages.py`](bot/handlers/messages.py).
- **Форум-группа** с темами: публикации и служебные потоки задаются id тем в `.env` (`TELEGRAM_TOPIC_*`). Бот публикует в нужные ветки согласно логике сервисов.
- Команды: `/start`, `/help`, `/files` — см. [`bot/main.py`](bot/main.py).

### Веб-хаб

- Открыть `http://<хост>:8000` (в LAN удобно узнать URL: [`scripts/print-web-lan-url.sh`](scripts/print-web-lan-url.sh)).
- **Вход:** указать свой числовой Telegram user id → одноразовый код в ЛС от бота → сессия cookie. Нужен активный участник в БД (whitelist / members).
- Права «админа» на сайте задаются `WEB_ADMIN_TELEGRAM_IDS` в `.env`.

### Операции и обслуживание

- Схема БД: [`db/schema.sql`](db/schema.sql), донакат патчей при старте: [`db/schema_patch.py`](db/schema_patch.py).
- Разовая догрузка саммари событий: `uv run python -m web.backfill_summaries` (см. комментарий в [`web/backfill_summaries.py`](web/backfill_summaries.py)).
- Файлы участников на диске: каталог `storage/` (в `.gitignore`).

## Как устроен репозиторий

| Путь | Назначение |
|------|------------|
| `bot/` | Точка входа бота, хендлеры, клавиатуры |
| `web/` | FastAPI-приложение, шаблоны Jinja2, статика |
| `db/` | Пул asyncpg, репозиторий запросов, SQL и патчи схемы |
| `services/` | Бизнес-логика: события, файлы, HR, компании, LLM, очки активности и др. |
| `prompts/` | Тексты промптов для LLM (отдельно от кода) |
| `config/` | JSON настроек очков активности и контекста для сценариев (не секреты) |
| `utils/` | Мелкие утилиты (slug, подписи в Telegram, таблицы) |

Поток данных в общих чертах: **Telegram** → хендлеры → **services** + **db/repo** → **PostgreSQL**; **веб** читает и пишет те же таблицы через `repo` и отдаёт HTML/JSON.

История небольших изменений по задачам — в [`docs/changelog/`](docs/changelog/) (файлы `change_*.md`).

## Сборка

- Образ: [`Dockerfile`](Dockerfile) — `uv sync --frozen`, команда по умолчанию — бот; в Compose для веба переопределена команда на `web.main`.
- Зависимости зафиксированы в [`uv.lock`](uv.lock); Python версии: [`.python-version`](.python-version).

## Лицензия и секреты

Секреты не коммитить: `.env`, ключи Google, `storage/`. Пример переменных — только в `.env.example`.
