# change_4

## Что добавлено / исправлено

- **Скачивание файла из библиотеки:** в `bot/handlers/callbacks.py` вместо несуществующего в PTB `FSInputFile` используется `InputFile` + открытие файла с диска — `send_document` снова работает.
- **`.env.example`:** переменные `GROQ_API_KEY`, `GOOGLE_SHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON_PATH`; пояснение про легаси `FILE_CATEGORIES`.

## Зачем

- Без `InputFile` контейнер падал с `ImportError` / не отдавал файлы по callback `fdl:`.
- Пример env должен совпадать с `config.py`, чтобы новый инсталл не гадал по ключам.

## Почему так

- В `python-telegram-bot` 21.x в публичном API есть `InputFile`, а не `FSInputFile`.

## Что улучшить позже

- Для очень больших файлов — `InputFile(..., read_file_handle=False)` и аккуратное закрытие дескриптора после ответа Telegram.
- Единый логгер по операциям (файлы, HR, voice) с `extra=` для трассировки.
