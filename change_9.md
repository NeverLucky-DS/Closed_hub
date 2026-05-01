# change_9

## Очки активности

- Файл **`config/activity_points.json`** — суммы за `hr_contact_confirmed`, `library_file_confirmed`, `event_published`, `interview_submitted` (дефолты в `services/activity_points.py`, если JSON битый/нет).
- Таблицы **`activity_ledger`**, поле **`members.activity_points`** (миграция 4).
- Начисления: подтверждённый HR, подтверждённый файл в библиотеке, опубликованное/сохранённое мероприятие, успешная запись опыта собеса.

## Файлы в библиотеке

- Колонки **`uploader_handle`**, **`confirmed_at`**; в `/files` в списке видно кто выложил и время.

## Google Sheets (HR)

- Название вкладки: **`normalize_company_sheet_title`** (`casefold`) — «Яндекс» и «яндекс» на одном листе.
- Логи **`metric type=sheets_workbook`** / **`sheets_append`**; предупреждение при **≥180** вкладках (лимит Google ~200).

## Собесы

- Кнопка **«Собесы»** → **Узнать** (inline-выбор компании → файл `.md`) / **Рассказать** (режим текста/голоса → **«На этом всё»** → Mistral → допись в `storage/interviews/{slug}.md`).
- Слаг для кириллических компаний: **`interview_company_slug`** (hash-префикс `co_…`).
- Очки за рассказ — по `interview_submitted` в JSON.

## Что проверить

- Миграция 4 на существующем volume.
- Права на каталог `storage/interviews` в Docker (уже внутри `./storage`).
