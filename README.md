# Closed Hub

Закрытая **инфраструктура** для небольшого сообщества: один **Telegram-бот** (личные сообщения + форум-группа с темами), **веб-интерфейс** на том же PostgreSQL и набор **сервисов** (маршрутизация контента, ИИ, файлы, синхронизация с таблицами). Репозиторий рассчитан на развёртывание у себя: Docker Compose, явные переменные окружения, без привязки к конкретному бренду или контенту.

## Быстрый старт

1. **Клонировать** репозиторий, установить [uv](https://github.com/astral-sh/uv) и (по желанию) Docker.
2. **Окружение:** `cp .env.example .env` и заполнить минимум `TELEGRAM_BOT_TOKEN`, `TELEGRAM_GROUP_CHAT_ID`, `MISTRAL_API_KEY`, `WEB_SESSION_SECRET`, `DATABASE_URL` (или полагаться на значения из примера для локального Postgres).
3. **База и процессы:**
   - `docker compose up -d` — поднимет Postgres, бота и веб. Внутри контейнера веб слушает **8000**; с хоста по умолчанию это **`WEB_HOST_PORT` → 8001** (`${WEB_HOST_PORT:-8001}:8000` в `docker-compose.yml`). То есть открывать `http://localhost:8001`, если не переопределяли порт.
   - Локально без Docker (частый вариант разработки): `uv sync`, затем `uv run python -m bot.main` и отдельно `uv run python -m web.main` — веб по умолчанию на **8000** (`http://localhost:8000`), если не задан другой порт в окружении/аргументах uvicorn.

Подробные переменные — в [`.env.example`](.env.example).

## Docker Compose и PostgreSQL

- **Учётные данные Postgres** задаются переменными `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` (подхватываются из окружения или из `.env` рядом с `docker-compose.yml`). Значения по умолчанию `closedhub` / `closedhub` / `closedhub` рассчитаны на **локальную** машину. **В продакшене обязательно задайте сильный `POSTGRES_PASSWORD`** и не оставляйте дефолт.
- Сервисы **bot** и **web** в Compose получают `DATABASE_URL` на `postgres:5432`, собранный из тех же `POSTGRES_*`, чтобы совпадать с контейнером Postgres.
- Схема при первом старте по-прежнему подключается как [`db/schema.sql`](db/schema.sql) → `docker-entrypoint-initdb.d`; healthcheck Postgres использует тот же пользователь и имя базы, что и переменные окружения контейнера.

## Как пользоваться

### Участник в Telegram

- Написать боту в **личку**: обработка текста, вложений, голосовых (при наличии `GROQ_API_KEY`), сценарии из [`bot/handlers/messages.py`](bot/handlers/messages.py).
- **Форум-группа** с темами: публикации и служебные потоки задаются id тем в `.env` (`TELEGRAM_TOPIC_*`). Бот публикует в нужные ветки согласно логике сервисов.
- Команды: `/start`, `/help`, `/files` — см. [`bot/main.py`](bot/main.py).

### Веб-хаб

- **Локально через `uv run … web.main`:** обычно `http://<хост>:8000`.
- **Через Docker Compose:** снаружи — порт из `WEB_HOST_PORT` (по умолчанию **8001**), внутри сети контейнеров приложение по-прежнему на 8000.
- В LAN удобно узнать URL: [`scripts/print-web-lan-url.sh`](scripts/print-web-lan-url.sh).
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

## VPS / Aeza

Для выноса на VPS (в том числе Aeza): **TLS** на границе (терминация на reverse proxy или у провайдера), **сильный `POSTGRES_PASSWORD`**, публичный трафик через **reverse proxy** к контейнеру с веб-приложением (и при необходимости отдельная политика доступа к боту/админке). **Проверки живости веба:** `GET /health` (процесс жив) и `GET /ready` (есть соединение с БД). Пошаговый план развёртывания: [`docs/plans/vps_aeza_hosting_plan.md`](docs/plans/vps_aeza_hosting_plan.md). Дополнительные заметки по хостингу: [`.cursor/skills/vps-aeza-hosting/SKILL.md`](.cursor/skills/vps-aeza-hosting/SKILL.md).

## Лицензия и секреты

Секреты не коммитить: `.env`, ключи Google, `storage/`. Пример переменных — только в `.env.example`.
