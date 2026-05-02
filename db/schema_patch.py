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
    (
        7,
        """
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS resume_path TEXT;
""",
    ),
    (
        8,
        """
ALTER TABLE events ADD COLUMN IF NOT EXISTS cover_image_path TEXT;
""",
    ),
    (
        9,
        """
CREATE TABLE IF NOT EXISTS event_reactions (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    telegram_user_id BIGINT NOT NULL,
    emoji TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (event_id, telegram_user_id)
);
CREATE INDEX IF NOT EXISTS idx_event_reactions_event ON event_reactions (event_id);

CREATE TABLE IF NOT EXISTS event_comments (
    id BIGSERIAL PRIMARY KEY,
    event_id BIGINT NOT NULL REFERENCES events (id) ON DELETE CASCADE,
    author_telegram_id BIGINT NOT NULL,
    body TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_event_comments_event ON event_comments (event_id, created_at);
""",
    ),
    (
        10,
        """
CREATE TABLE IF NOT EXISTS hackathon_teams (
    id BIGSERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    max_members INT NOT NULL CHECK (max_members >= 2 AND max_members <= 30),
    creator_telegram_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hackathon_team_members (
    team_id BIGINT NOT NULL REFERENCES hackathon_teams (id) ON DELETE CASCADE,
    telegram_user_id BIGINT NOT NULL,
    role TEXT NOT NULL DEFAULT 'member',
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, telegram_user_id)
);

CREATE TABLE IF NOT EXISTS hackathon_applications (
    id BIGSERIAL PRIMARY KEY,
    team_id BIGINT NOT NULL REFERENCES hackathon_teams (id) ON DELETE CASCADE,
    applicant_telegram_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, applicant_telegram_id)
);

CREATE INDEX IF NOT EXISTS idx_hackathon_app_team_status
    ON hackathon_applications (team_id, status);
""",
    ),
    (
        11,
        """
CREATE TABLE IF NOT EXISTS companies (
    id BIGSERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    description TEXT,
    photo_paths JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_by BIGINT NOT NULL REFERENCES members (telegram_user_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_companies_updated ON companies (updated_at DESC);

ALTER TABLE hr_contacts ADD COLUMN IF NOT EXISTS company_id BIGINT
    REFERENCES companies (id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS idx_hr_contacts_company ON hr_contacts (company_id)
    WHERE company_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS company_files (
    id BIGSERIAL PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    file_id BIGINT NOT NULL REFERENCES files (id) ON DELETE CASCADE,
    linked_by BIGINT NOT NULL REFERENCES members (telegram_user_id),
    note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (company_id, file_id)
);
CREATE INDEX IF NOT EXISTS idx_company_files_company ON company_files (company_id);

CREATE TABLE IF NOT EXISTS company_interview_reviews (
    id BIGSERIAL PRIMARY KEY,
    company_id BIGINT NOT NULL REFERENCES companies (id) ON DELETE CASCADE,
    author_telegram_id BIGINT NOT NULL REFERENCES members (telegram_user_id),
    body TEXT NOT NULL,
    hr_contact_id BIGINT REFERENCES hr_contacts (id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT company_interview_reviews_body_or_hr CHECK (
        char_length(trim(body)) >= 10 OR hr_contact_id IS NOT NULL
    )
);
CREATE INDEX IF NOT EXISTS idx_company_reviews_company
    ON company_interview_reviews (company_id, created_at DESC);
""",
    ),
    (
        12,
        """
CREATE INDEX IF NOT EXISTS idx_files_sha256_active
    ON files (sha256)
    WHERE status NOT IN ('deleted', 'cancelled');
""",
    ),
    (
        13,
        """
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS hf_url TEXT;
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS kaggle_url TEXT;
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS leetcode_url TEXT;
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS education_institution TEXT;
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS education_year_from INT;
ALTER TABLE member_profiles ADD COLUMN IF NOT EXISTS education_year_to INT;
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
