"""Идемпотентные патчи схемы для уже существующих БД (Docker volume)."""

MIGRATION_ID = 2

PATCH_SQL = """
CREATE TABLE IF NOT EXISTS file_categories (
    slug TEXT PRIMARY KEY,
    label_ru TEXT NOT NULL,
    created_by BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO file_categories (slug, label_ru) VALUES
    ('ml', 'Machine Learning (общее)'),
    ('dl', 'Deep Learning'),
    ('algorithms', 'Алгоритмы и структуры данных'),
    ('math_analysis', 'Математический анализ'),
    ('linear_algebra', 'Линейная алгебра'),
    ('probability', 'Теория вероятностей и статистика'),
    ('nlp', 'NLP'),
    ('cv', 'Computer Vision'),
    ('other', 'Другое / смешанное')
ON CONFLICT (slug) DO NOTHING;

ALTER TABLE files ADD COLUMN IF NOT EXISTS original_filename TEXT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS subject_tags TEXT;
"""


async def apply_pending_patches(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id INT PRIMARY KEY,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )
        row = await conn.fetchrow("SELECT 1 FROM schema_migrations WHERE id = $1", MIGRATION_ID)
        if row:
            return
        await conn.execute(PATCH_SQL)
        await conn.execute("INSERT INTO schema_migrations (id) VALUES ($1)", MIGRATION_ID)
