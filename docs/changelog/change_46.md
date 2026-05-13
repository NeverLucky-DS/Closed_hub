# change_46 — прокси для Telegram Bot API

## Что добавлено

- Переменная **`TELEGRAM_PROXY_URL`**: HTTP или SOCKS5 URL для всех вызовов `api.telegram.org` (бот через `HTTPXRequest`, сайт — `sendMessage` / `deleteMessage`).
- Несколько URL **через запятую**: запасные; веб перебирает при сетевых ошибках; процесс бота использует **только первый** (ограничение одного `HTTPXRequest`).
- Зависимость **`httpx[socks]`** для SOCKS5.

## Зачем

Доступ к Telegram API без прямого канала: один настраиваемый прокси-контур для бота и веб-OTP.

## Важно (MTProxy)

Рекламы вида *Server + Port + Secret (ee…)* — это **MTProto MTProxy** (как в Telegram Desktop). Библиотека бота ходит по **HTTPS** и понимает только **HTTP CONNECT** и **SOCKS5**. Секрет вставить в `TELEGRAM_PROXY_URL` нельзя; нужен локальный клиент (VPN/TUN), который отдаёт `socks5://127.0.0.1:…`, или провайдер с обычным SOCKS/HTTP URL.

## Что улучшить позже

- Опционально: тот же прокси только для Telegram внутри общего `httpx` кода (сейчас Mistral/Groq без изменений).
- Ротация прокси для бота без перезапуска (сложнее, свой transport).
