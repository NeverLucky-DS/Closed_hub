"""Идемпотентные патчи схемы для уже существующих БД (Docker volume)."""

MIGRATIONS: list[tuple[int, str]] = [
    (
        2,
        """
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
""",
    ),
    (
        3,
        """
ALTER TABLE hr_contacts ADD COLUMN IF NOT EXISTS contact_ref TEXT;
DO $hr_mig$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'hr_contacts' AND column_name = 'telegram_uid'
  ) THEN
    UPDATE hr_contacts SET contact_ref = telegram_uid::text WHERE contact_ref IS NULL;
  END IF;
END $hr_mig$;
UPDATE hr_contacts SET contact_ref = 'legacy' WHERE contact_ref IS NULL;
ALTER TABLE hr_contacts DROP COLUMN IF EXISTS telegram_uid;
ALTER TABLE hr_contacts ALTER COLUMN contact_ref SET NOT NULL;
""",
    ),
    (
        4,
        """
ALTER TABLE members ADD COLUMN IF NOT EXISTS activity_points INT NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS activity_ledger (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    points INT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_activity_ledger_user ON activity_ledger (telegram_user_id, created_at DESC);

ALTER TABLE files ADD COLUMN IF NOT EXISTS uploader_handle TEXT;
ALTER TABLE files ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMPTZ;
""",
    ),
    (
        5,
        """
ALTER TABLE events ADD COLUMN IF NOT EXISTS starts_at TIMESTAMPTZ;
ALTER TABLE events ADD COLUMN IF NOT EXISTS ends_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS web_login_codes (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    code_hash TEXT NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    consumed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_web_login_codes_user_expires
    ON web_login_codes (telegram_user_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS member_profiles (
    telegram_user_id BIGINT PRIMARY KEY REFERENCES members (telegram_user_id) ON DELETE CASCADE,
    display_name TEXT,
    bio TEXT,
    github_url TEXT NOT NULL DEFAULT 'https://github.com/',
    photo_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
""",
    ),
    (
        6,
        """
ALTER TABLE events ADD COLUMN IF NOT EXISTS ai_summary TEXT;
""",
    ),
]


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
        for mid, sql in MIGRATIONS:
            row = await conn.fetchrow("SELECT 1 FROM schema_migrations WHERE id = $1", mid)
            if row:
                continue
            await conn.execute(sql)
            await conn.execute("INSERT INTO schema_migrations (id) VALUES ($1)", mid)
