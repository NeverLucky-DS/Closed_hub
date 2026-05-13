# Change 63

## Что изменилось

- Добавлена session-based CSRF-защита для POST/PATCH/DELETE: формы отправляют hidden `csrf_token`, fetch-запросы — `X-CSRF-Token`.
- `/logout` переведён с GET на POST-форму в desktop и mobile-навигации.
- Добавлены env-настройки `WEB_COOKIE_SECURE`, `WEB_PROXY_HEADERS`, `WEB_FORWARDED_ALLOW_IPS`.
- Для ответов добавлены базовые security headers, а raw-файлы библиотеки открываются inline только для PDF и безопасных image MIME. Остальное отдаётся как attachment.
- Проверки путей под storage переведены на `Path.is_relative_to`.

## Зачем

Так веб-хаб безопаснее публиковать за HTTPS reverse proxy на VPS: cookie можно сделать Secure, proxy headers не доверяются всем по умолчанию, state-changing запросы требуют CSRF token, а браузер не пытается inline-открывать потенциально опасные типы файлов.
