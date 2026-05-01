# change_18

## Что сделано

- **Веб-хаб** (`web/`): FastAPI + Jinja2, тёмная минималистичная тема (`web/static/css/base.css`). Вход в два шага: Telegram UID → одноразовый код в личку от бота (HTTP `sendMessage` тем же токеном), сессия в подписанной cookie.
- **Страницы**: библиотека файлов с фильтром по теме и просмотром (PDF/картинки в iframe/img), лента мероприятий, компактная полоска «Сегодня», список людей и карточка профиля, редактирование своего профиля (bio, имя, обязательный GitHub, до 3 фото).
- **БД** (миграция `schema_patch` id=5): `web_login_codes`, `member_profiles` (в т.ч. `photo_paths` JSONB), в `events` поля `starts_at` / `ends_at`; запросы ленты с приоритетом «скоро конец» и «недавно добавлено» в [db/repo.py](db/repo.py).
- **Бот**: кнопка «Сайт», текст с UID и пояснением про код; опционально ссылка из `WEB_PUBLIC_BASE_URL`.
- **Инфра**: зависимости в [pyproject.toml](pyproject.toml), сервис `web` в [docker-compose.yml](docker-compose.yml), `profile_root()` в [services/file_storage.py](services/file_storage.py).

## Зачем

- Закрытый веб-интерфейс к тем же данным, что и бот, без отдельных паролей — только Telegram.

## Почему так

- Отдельный процесс `web` рядом с `bot`, общие Postgres и `./storage`.
- Код входа хранится как HMAC-SHA256 с `WEB_SESSION_SECRET`, одноразовый с отметкой `consumed_at`.

## Что улучшить позже

- Заполнять `ends_at` при создании мероприятия (LLM или ручной ввод).
- Rate limit по IP в Redis/БД, CSP и HTTPS-only для cookie в проде.
- Пакет `tool.uv.package = true` для установки entry points, если понадобится.
