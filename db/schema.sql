CREATE TABLE IF NOT EXISTS members (
    telegram_user_id BIGINT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'revoked')),
    invited_by BIGINT REFERENCES members (telegram_user_id) ON DELETE SET NULL,
    activity_points INT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS activity_ledger (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    reason TEXT NOT NULL,
    points INT NOT NULL,
    meta JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_activity_ledger_user ON activity_ledger (telegram_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS whitelist_users (
    telegram_user_id BIGINT PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS bot_sessions (
    telegram_user_id BIGINT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'idle',
    payload JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inbound_messages (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL,
    chat_id BIGINT NOT NULL,
    message_id INT,
    text_content TEXT,
    has_document BOOLEAN NOT NULL DEFAULT false,
    file_id TEXT,
    mime_type TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_inbound_user_created ON inbound_messages (telegram_user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id BIGSERIAL PRIMARY KEY,
    raw_text TEXT NOT NULL,
    normalized_title TEXT,
    source_user_id BIGINT NOT NULL,
    status TEXT NOT NULL DEFAULT 'published',
    published_message_id INT,
    starts_at TIMESTAMPTZ,
    ends_at TIMESTAMPTZ,
    ai_summary TEXT,
    cover_image_path TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    resume_path TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_contacts (
    id BIGSERIAL PRIMARY KEY,
    contact_ref TEXT NOT NULL,
    status TEXT NOT NULL,
    company TEXT,
    role_hint TEXT,
    vacancies_hint TEXT,
    summary TEXT,
    source_user_id BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hr_contact_context (
    id BIGSERIAL PRIMARY KEY,
    hr_contact_id BIGINT NOT NULL REFERENCES hr_contacts (id) ON DELETE CASCADE,
    text_content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_hr_context_contact ON hr_contact_context (hr_contact_id, created_at);

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

CREATE TABLE IF NOT EXISTS schema_migrations (
    id INT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS files (
    id BIGSERIAL PRIMARY KEY,
    storage_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    mime_type TEXT,
    suggested_category TEXT,
    confirmed_category TEXT,
    status TEXT NOT NULL DEFAULT 'processing',
    summary TEXT,
    extracted_text_preview TEXT,
    original_filename TEXT,
    subject_tags TEXT,
    uploader_handle TEXT,
    confirmed_at TIMESTAMPTZ,
    uploaded_by BIGINT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS llm_calls (
    id BIGSERIAL PRIMARY KEY,
    purpose TEXT NOT NULL,
    model TEXT,
    prompt_tokens INT,
    completion_tokens INT,
    latency_ms INT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
