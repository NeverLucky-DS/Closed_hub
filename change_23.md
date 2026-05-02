# change_23

## Что сделано

- **Новости (`/feed`):** карточки в одну колонку: слева превью, справа мета («N мин назад» · Хаб · до …), заголовок, саммари, полный текст в `<details>`. Сортировка только **`created_at DESC`** среди ещё не просроченных (`ends_at` NULL или в будущем).
- **Выжимка (`/today`):** компактный список с буллетами (как референс): приоритет свежим и близким дедлайнам; клик по строке открывает диалог **дата окончания актуальности** (`PATCH /api/events/{id}/ends_at`, дата `YYYY-MM-DD`, сохраняется конец дня UTC). Ссылка «в ленту» ведёт на якорь карточки.
- **БД:** миграция **8** — `events.cover_image_path` (пока бот не пишет; задел под картинку из группы). `GET /api/events/{id}/cover` отдаёт файл из `storage` для залогиненных.
- **Заглушки превью:** каталог `web/static/news-placeholders/`, имена **`placeholder-1` … `placeholder-5`** + расширение `.webp`/`.jpg`/`.png`; выбор **детерминированно** `(event_id % 5) + 1`. В `.gitignore` игнорируются все файлы в папке, кроме `.gitkeep` и `NAMES.txt`.
- **Навигация:** «Мероприятия» → **Новости**, «Сегодня» → **Выжимка**.

## Промпт для генерации 5 заглушек (один стиль, разные оттенки)

Используй в Midjourney / DALL·E / SD и т.п. (на английском обычно стабильнее):

> **Series of 5 square or 4:3 editorial news thumbnails, same visual language: minimal abstract geometric composition, soft gradient background (deep indigo → violet OR slate → teal), single subtle focal shape (paper fold, soft glow arc, or blurred circle), no text, no logos, no people, no readable UI. Premium dark tech-news aesthetic, high contrast, slight grain, consistent lighting across all five, only hue shifts between images (1 cool blue, 2 purple, 3 teal, 4 warm amber accent, 5 rose/magenta). Safe for small thumbnail crop.**

После генерации обрежь до **~400×300** или **1:1**, сохрани как `placeholder-1.jpg` … `placeholder-5.jpg` в `web/static/news-placeholders/`.

## Что улучшить позже

- Заполнять `cover_image_path` из бота при анонсе с фото.
- Редактирование даты прямо с карточки в ленте без перехода в выжимку.
