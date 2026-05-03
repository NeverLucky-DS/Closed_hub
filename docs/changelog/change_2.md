## Что добавлено

- **Темы форума** в конфиге: `TELEGRAM_TOPIC_NEWS=4`, `TELEGRAM_TOPIC_DISCUSSION=3`, `TELEGRAM_TOPIC_RATING=2` (из ссылок `t.me/c/…/4` и т.д.). Публикация мероприятий идёт в **Новости** (`TELEGRAM_TOPIC_NEWS` или устаревший `TELEGRAM_EVENTS_TOPIC_ID`).
- **`TELEGRAM_GROUP_CHAT_ID`** может быть числом `-100…` или строкой `@username` супергруппы (как в Bot API).
- **UV**: [`pyproject.toml`](pyproject.toml), [`uv.lock`](uv.lock), [`.python-version`](.python-version) (`3.12`), флаг `package = false` для приложения без setuptools-пакета.
- **DVC**: инициализация репозитория, [`dvc.yaml`](dvc.yaml) с подсказками, [`artifacts/`](artifacts/) для будущих тяжёлых данных, [`/.dvcignore`](.dvcignore).
- **Docker**: сборка через `uv sync` из lock-файла, [`.dockerignore`](.dockerignore).
- **`.env` / `.env.example`**: все переменные с комментариями по назначению.

## Зачем

Единый lock зависимостей (UV), воспроизводимые окружения и Docker; DVC — заготовка под дампы БД / большие файлы без раздувания git; явные id тем под ваш форум.

## Почему так

- Минимум дублирования: зависимости в одном месте (`pyproject.toml`), `requirements.txt` оставлен как зеркало для `pip`.
- DVC без фиктивных pipeline — только `stages: {}` и документация в `dvc.yaml`, чтобы не ломать `dvc repro`.

## Что улучшить позже

- Подключить `dvc remote` (S3/SSH) для команды.
- Использовать `TELEGRAM_TOPIC_DISCUSSION` / `TELEGRAM_TOPIC_RATING` в коде (рейтинг, обсуждения).
- Скрипт `uv run …` в Makefile или `task` для одной команды «поднять всё».

## Запуск

```bash
uv sync                    # runtime
uv sync --group dev        # + DVC для разработки
uv run python -m bot.main
# или
docker compose up --build
```
