# change_8

## Что сделано

- **Docker + Google Sheets:** в `docker-compose.yml` ключ монтируется в `/app/secrets/google-service-account.json`, `GOOGLE_SERVICE_ACCOUNT_JSON_PATH` в сервисе `bot` зафиксирован на этот путь. На хосте путь к файлу задаётся через `GOOGLE_SERVICE_ACCOUNT_JSON_HOST` (по умолчанию `./closeml-fc7387cd4ce5.json`).
- **`.env.example`:** разделены путь для `uv run` и `GOOGLE_SERVICE_ACCOUNT_JSON_HOST` для compose.
- **`append_hr_contact_row`:** возвращает `skipped` / `ok` / `error`; при `FileNotFoundError` — явный лог с подсказкой.
- **Callback «Верно» по HR:** текст после сохранения зависит от результата выгрузки в Sheets (успех / ошибка / не настроено).

## Почему не писало в таблицу

В контейнере не было файла `./closeml-fc7387cd4ce5.json` — ключ не копируется в образ и не был смонтирован.
