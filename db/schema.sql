CREATE TABLE IF NOT EXISTS members (
    telegram_user_id BIGINT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'revoked')),
    invited_by BIGINT REFERENCES members (telegram_user_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
