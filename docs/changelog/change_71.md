# change_71: Семантический поиск через Jina Embeddings + pgvector

## Что изменилось

### Инфраструктура
- `docker-compose.yml`: Postgres образ сменён с `postgres:16-alpine` на `pgvector/pgvector:pg16` для поддержки расширения `vector`.
- `db/schema.sql`: добавлены `CREATE EXTENSION IF NOT EXISTS vector`, колонки `embedding vector(256)`, `embedding_model`, `embedding_text_hash`, `embedding_updated_at` для таблиц `resource_links` и `files`, а также HNSW-индексы для cosine distance.
- `db/schema_patch.py`: миграция №17 добавляет те же колонки и индексы на существующие БД через `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.

### Сервис embeddings
- Новый файл `services/embedding_service.py`:
  - `embed_texts(texts, task)` — POST в Jina API, модель `jina-embeddings-v5-text-small`, `dimensions=256`, `normalized=true`.
  - `build_link_embedding_text(...)` / `build_file_embedding_text(...)` — сборка текста для кодирования.
  - `text_hash(text)` — SHA-1 чтобы не кодировать повторно.
  - `refresh_resource_link_embedding(pool, id)` / `refresh_file_embedding(pool, id)` — обновляют вектор в БД.
  - Использует уже существующий `JINA_API_KEY` из конфига.

### Кодирование новых материалов
- `web/app.py` (`resources_add_link`): после `INSERT` в `resource_links` вызывает `refresh_resource_link_embedding`. Ошибка API не мешает редиректу.
- `bot/handlers/callbacks.py` (callback `rlok`): то же для ссылок из бота.
- `services/files_service.py` (`finalize_file_to_library`): после перевода файла в `confirmed` вызывает `refresh_file_embedding`. Ошибка не мешает подтверждению.

### Новые repo-функции (`db/repo.py`)
- `get_resource_link_with_topic(pool, id)` — ссылка с путём темы и hash.
- `update_resource_link_embedding(pool, id, vec, hash)` — сохранение вектора.
- `update_file_embedding(pool, id, vec, hash)` — сохранение вектора для файла.
- `semantic_search_resource_links(pool, query_vector, limit)` — `ORDER BY embedding <=> $1::vector`.
- `semantic_search_library_files(pool, query_vector, limit)` — то же для файлов.

### Подключение поиска
- `services/search_service.py`: добавлена `query_vector(text)` — кодирует запрос с `task=retrieval.query`.
- `bot/handlers/library_cmd.py` (`/search`, `/files`, `/links`): сначала пробует semantic search, fallback на ILIKE.
- `bot/handlers/messages.py` (state `resource_search`): то же.
- `web/app.py` (`/resources?q=`): при текстовом запросе без выбранной темы — semantic search, fallback на ILIKE.

### Backfill
- Новый скрипт `scripts/backfill_embeddings.py`:
  - Кодирует все старые ссылки и файлы батчами (по умолчанию 20).
  - Пропускает записи, у которых `embedding_text_hash` совпадает (текст не менялся).
  - Флаги `--only links`, `--only files`, `--batch-size N`.
  - Запуск: `uv run python scripts/backfill_embeddings.py`.

## Обоснование
Семантический поиск через cosine similarity находит релевантные материалы даже без точного совпадения слов. pgvector — нативное решение для PostgreSQL без лишних зависимостей. Все точки отказа защищены: если Jina API недоступна, система продолжает работать через ILIKE-поиск.

## Действия после деплоя
1. Обновить Postgres образ: `docker compose pull postgres && docker compose up -d postgres`
2. Применить миграции (происходит автоматически при старте бота/веба).
3. Запустить backfill: `uv run python scripts/backfill_embeddings.py`.
