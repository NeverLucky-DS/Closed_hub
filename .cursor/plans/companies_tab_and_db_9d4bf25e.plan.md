---
name: Companies tab and DB
overview: Ввести сущность «компания» в PostgreSQL, связать с подтверждёнными HR, библиотечными файлами и отзывами о собесах; добавить раздел «Компании» в веб-интерфейс с превью логотипов и простыми формами без лишней абстракции.
todos:
  - id: migration-companies
    content: "Патч БД 11: companies, company_files, company_interview_reviews; hr_contacts.company_id; индексы + schema.sql"
    status: completed
  - id: storage-repo
    content: company_root(), repo-функции, безопасная раздача /media/company/...
    status: completed
  - id: web-companies-ui
    content: "Маршруты и шаблоны: список, создание, деталь + формы отзыв/файл/HR"
    status: completed
  - id: change-md
    content: change_N.md с описанием решения и улучшений позже
    status: completed
isProject: false
---

# Компании на сайте: модель данных и UI

## Цели

- Единая база: **компания** как узел, к которому крепятся **HR-контакты** (из [`hr_contacts`](db/schema.sql)), **файлы** (из [`files`](db/schema.sql)), **отзывы о собесе** (новая сущность), плюс **фото компании** для превью в списке.
- Доступ: **любой активный участник** (как вы выбрали) — создание компаний, привязки и отзывы через существующую сессию [`require_uid_api`](web/app.py).

## Модель данных (PostgreSQL)

```mermaid
erDiagram
  companies ||--o{ hr_contacts : "company_id"
  companies ||--o{ company_files : "has"
  companies ||--o{ company_interview_reviews : "has"
  files ||--o{ company_files : "linked"
  hr_contacts ||--o{ company_interview_reviews : "optional_recruiter"
  members ||--o{ companies : "created_by"
  members ||--o{ company_interview_reviews : "author"

  companies {
    bigserial id PK
    text slug UK
    text name
    text description
    jsonb photo_paths
    bigint created_by FK
    timestamptz updated_at
  }
  company_files {
    bigserial id PK
    bigint company_id FK
    bigint file_id FK
    bigint linked_by FK
    text note
  }
  company_interview_reviews {
    bigserial id PK
    bigint company_id FK
    bigint author_telegram_id FK
    text body
    bigint hr_contact_id FK_null
  }
```

1. **`companies`**
   - `slug` — уникальный, для URL (генерация из названия + суффикс при коллизии).
   - `photo_paths` — JSONB массив относительных путей, **тот же паттерн**, что у [`member_profiles.photo_paths`](db/schema.sql): хранение списка, валидация без `..`.
   - Превью в списке: первый путь из `photo_paths` (как [`_event_thumb_url`](web/app.py) / профиль).

2. **`hr_contacts.company_id`**
   - `BIGINT NULL REFERENCES companies(id) ON DELETE SET NULL`.
   - Поле `company` (текст из LLM) **оставить** — подпись/фолбэк и для Google Sheets; привязка к карточке на сайте через FK.
   - На карточке компании показывать только контакты с `status = 'confirmed'` и `company_id = этой компании`.

3. **`company_files`**
   - Связь M:N: `UNIQUE(company_id, file_id)`.
   - В UI давать только файлы со `status = 'confirmed'` (как [`library_file_raw`](web/app.py)).
   - Опционально `note` (короткая подпись: «оффер», «тестовое»).

4. **`company_interview_reviews`**
   - «Единая база» для **отзыва** и **привязки эйчара**: `body` (текст отзыва), `hr_contact_id` **NULL** — если указан, в интерфейсе показать карточку/краткое саммари этого HR рядом с отзывом.
   - Ограничение: либо `length(body) >= минимум`, либо задан `hr_contact_id` (чтобы можно было коротко отметить «собес с этим контактом»); на MVP достаточно `body NOT NULL` с минимальной длиной 10–20 символов и опциональный HR.

**Миграция:** новый номер в [`db/schema_patch.py`](db/schema_patch.py) (после `10`) + зеркально обновить [`db/schema.sql`](db/schema.sql) для чистых установок.

**Файлы на диске:** функция `company_root()` в [`services/file_storage.py`](services/file_storage.py) → `{file_storage_path}/companies/{company_id}/...`, по аналогии с `profile_root()`.

## Веб-слой

- **Навигация:** пункт «Компании» в [`web/templates/base.html`](web/templates/base.html) (десктоп и мобильная навигация, если дублируется).
- **Маршруты** в [`web/app.py`](web/app.py) (Jinja, без SPA):
  - `GET /companies` — сетка карточек: имя, превью (первое фото или плейсхолдер), бейджи счётчиков: HR / файлы / отзывы.
  - `GET/POST /companies/new` — название, описание, до N фото (переиспользовать логику лимитов/ MIME из [`page_me_save`](web/app.py): те же `web_max_profile_photo_mb` или отдельный лимит в `config`, по желанию).
  - `GET /companies/{slug}` — детальная страница с блоками: описание + галерея, список привязанных HR (саммари, роль, вакансии — без агрессивного показа `contact_ref` в первой строке; для закрытого хаба можно выводить как сейчас в боте или маскировать — зафиксировать в коде один вариант).
  - Формы POST под детальной страницей (или отдельные мини-страницы): **добавить отзыв**; **привязать файл** (`file_id` + опционально note — можно подсказка «возьми id из URL библиотеки»); **привязать HR** — `<select>` по `confirmed` контактам (список из нового `repo.list_hr_contacts_for_company_picker` с LIMIT и поиском позже).
- **Раздача медиа:** `GET /media/company/{company_id}/{filename}` с проверкой вхождения файла в `photo_paths` компании (копия паттерна [`profile_media`](web/app.py)).
- **Репозиторий:** функции в [`db/repo.py`](db/repo.py): `insert_company`, `get_company_by_slug`, `list_companies_with_counts`, `link_file_to_company`, `insert_interview_review`, `set_hr_contact_company`, `list_hr_for_company`, и т.д.

## Удобство и скорость (MVP)

- Список компаний с **счётчиками в одном запросе** (`LEFT JOIN` + `COUNT` или подзапросы) — без N+1.
- Привязка файла: **числовой `file_id`** + кнопка «открыть библиотеку в новой вкладке» — без тяжёлого встроенного поиска на первом этапе; при необходимости второй итерацией — dropdown последних загруженных пользователем файлов (`uploaded_by = uid`).
- Создание компании и привязки **без модерации** (закрытый хаб); при спаме позже — лимиты или флаг админа.

## Что сознательно не делаем в этой итерации

- Автосоздание компаний из уникальных `hr_contacts.company` (можно отдельным скриптом/backfill).
- Интеграция бота (кнопка «привязать к компании» после подтверждения HR).
- Сложный full-text поиск по отзывам.

## После реализации

- Файл **`change_*.md`** по правилам проекта с кратким описанием схемы и страниц.
