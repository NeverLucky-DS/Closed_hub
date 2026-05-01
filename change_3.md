## Что добавлено

- В [`docker-compose.yml`](docker-compose.yml) у сервиса `bot` задано `environment.DATABASE_URL` с хостом **`postgres`**, чтобы контейнер бота не ходил в `localhost` (там нет Postgres).
- У `postgres` добавлен **healthcheck**; `bot` стартует с `depends_on: condition: service_healthy`, чтобы не ловить гонку при первом запуске.

## Зачем

Ошибка `Connect call failed ('127.0.0.1', 5432)` возникала из‑за `.env` с `localhost`: внутри контейнера это не другой сервис compose.

## Почему так

Переопределение только в compose сохраняет удобный `.env` для локального `uv run` с `localhost`.

## Что улучшить позже

При смене пользователя/пароля БД дублировать их и в `postgres.environment`, и в `DATABASE_URL` бота (или вынести в один `.env` с подстановкой).
