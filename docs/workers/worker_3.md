# Worker 3 — минимальный CI

## Что добавлено

Файл [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml):

- **Триггеры:** `push` и `pull_request` только для ветки `main` (отдельно `master` не указывался — в репозитории целевая ветка `main`).
- **Окружение:** `ubuntu-latest`.
- **Установка uv:** официальный экшен [`astral-sh/setup-uv@v5`](https://github.com/astral-sh/setup-uv) с `python-version-file: .python-version` (фиксированная минорная версия Python в репозитории).
- **Зависимости:** `uv sync --frozen` по закреплённому `uv.lock`.
- **Дымовой тест:** `uv run python -c "from web.app import app; assert app.title"` — проверяется, что приложение импортируется и у `FastAPI` задан `title`.

## Переменные окружения в job (не Secrets)

Импорт `web.app` на этапе загрузки модуля вызывает `get_settings()` и настраивает `SessionMiddleware` с секретом сессии. Без значений для обязательных полей настроек импорт падает. В workflow заданы **литеральные плейсхолдеры** в `env` job (`TELEGRAM_BOT_TOKEN`, `MISTRAL_API_KEY`, `WEB_SESSION_SECRET`): это не GitHub Secrets и не обращения к внешним API, только чтобы прошла инициализация модуля.

## Чего нет намеренно

- Нет `secrets:` и не настроены вызовы к внешним API (кроме установки пакетов через PyPI при `uv sync`).
