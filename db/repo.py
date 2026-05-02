from __future__ import annotations

import json
from typing import Any

import asyncpg
from asyncpg.exceptions import CheckViolationError, UniqueViolationError

_UNSET = object()


async def seed_whitelist_and_members(pool: asyncpg.Pool, user_ids: list[int]) -> None:
    if not user_ids:
        return
    async with pool.acquire() as conn:
        async with conn.transaction():
            for uid in user_ids:
                await conn.execute(
                    """
                    INSERT INTO whitelist_users (telegram_user_id) VALUES ($1)
                    ON CONFLICT (telegram_user_id) DO NOTHING
                    """,
                    uid,
                )
                await conn.execute(
                    """
                    INSERT INTO members (telegram_user_id, status, invited_by)
                    VALUES ($1, 'active', NULL)
                    ON CONFLICT (telegram_user_id) DO NOTHING
                    """,
                    uid,
                )


async def is_whitelist(pool: asyncpg.Pool, telegram_user_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM whitelist_users WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        return row is not None


async def member_status(pool: asyncpg.Pool, telegram_user_id: int) -> str | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM members WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        return row["status"] if row else None


async def add_or_activate_member(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    invited_by: int | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO members (telegram_user_id, status, invited_by)
            VALUES ($1, 'active', $2)
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET status = 'active', invited_by = COALESCE(EXCLUDED.invited_by, members.invited_by)
            """,
            telegram_user_id,
            invited_by,
        )


async def get_session(pool: asyncpg.Pool, telegram_user_id: int) -> tuple[str, dict[str, Any]]:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT state, payload FROM bot_sessions WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        if not row:
            return "idle", {}
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        return row["state"], dict(payload or {})


async def set_session(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    state: str,
    payload: dict[str, Any] | None = None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO bot_sessions (telegram_user_id, state, payload, updated_at)
            VALUES ($1, $2, $3::jsonb, now())
            ON CONFLICT (telegram_user_id) DO UPDATE
            SET state = EXCLUDED.state,
                payload = EXCLUDED.payload,
                updated_at = now()
            """,
            telegram_user_id,
            state,
            json.dumps(payload or {}),
        )


async def clear_session(pool: asyncpg.Pool, telegram_user_id: int) -> None:
    await set_session(pool, telegram_user_id, "idle", {})


async def log_inbound(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    chat_id: int,
    message_id: int | None,
    text_content: str | None,
    has_document: bool,
    file_id: str | None,
    mime_type: str | None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO inbound_messages
            (telegram_user_id, chat_id, message_id, text_content, has_document, file_id, mime_type)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            telegram_user_id,
            chat_id,
            message_id,
            text_content,
            has_document,
            file_id,
            mime_type,
        )
        return int(row["id"])


async def recent_inbound_texts(pool: asyncpg.Pool, telegram_user_id: int, limit: int = 30) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT text_content FROM inbound_messages
            WHERE telegram_user_id = $1 AND text_content IS NOT NULL AND text_content <> ''
            ORDER BY created_at DESC
            LIMIT $2
            """,
            telegram_user_id,
            limit,
        )
        return [r["text_content"] for r in reversed(rows)]


async def recent_events_texts(pool: asyncpg.Pool, limit: int = 25) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT raw_text FROM events ORDER BY created_at DESC LIMIT $1
            """,
            limit,
        )
        return [r["raw_text"] for r in rows]


async def insert_event(
    pool: asyncpg.Pool,
    raw_text: str,
    normalized_title: str | None,
    source_user_id: int,
    status: str = "published",
    published_message_id: int | None = None,
    starts_at: Any | None = None,
    ends_at: Any | None = None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO events (
                raw_text, normalized_title, source_user_id, status, published_message_id,
                starts_at, ends_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            raw_text,
            normalized_title,
            source_user_id,
            status,
            published_message_id,
            starts_at,
            ends_at,
        )
        return int(row["id"])


async def update_event_published(pool: asyncpg.Pool, event_id: int, message_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE events SET published_message_id = $2, status = 'published' WHERE id = $1
            """,
            event_id,
            message_id,
        )


async def create_hr_contact_draft(
    pool: asyncpg.Pool,
    contact_ref: str,
    source_user_id: int,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO hr_contacts (contact_ref, status, source_user_id)
            VALUES ($1, 'awaiting_context', $2)
            RETURNING id
            """,
            contact_ref,
            source_user_id,
        )
        return int(row["id"])


async def append_hr_context(pool: asyncpg.Pool, hr_contact_id: int, text_content: str) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO hr_contact_context (hr_contact_id, text_content) VALUES ($1, $2)
            """,
            hr_contact_id,
            text_content,
        )
        await conn.execute(
            """
            UPDATE hr_contacts SET updated_at = now() WHERE id = $1
            """,
            hr_contact_id,
        )


async def get_hr_context_lines(pool: asyncpg.Pool, hr_contact_id: int) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT text_content FROM hr_contact_context
            WHERE hr_contact_id = $1 ORDER BY created_at ASC
            """,
            hr_contact_id,
        )
        return [r["text_content"] for r in rows]


async def get_open_hr_draft_for_user(pool: asyncpg.Pool, source_user_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT * FROM hr_contacts
            WHERE source_user_id = $1 AND status = 'awaiting_context'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            source_user_id,
        )


async def abandon_awaiting_hr_drafts(pool: asyncpg.Pool, source_user_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE hr_contacts SET status = 'abandoned', updated_at = now()
            WHERE source_user_id = $1 AND status = 'awaiting_context'
            """,
            source_user_id,
        )


async def abandon_hr_contact_by_id(
    pool: asyncpg.Pool, hr_contact_id: int, source_user_id: int
) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE hr_contacts SET status = 'abandoned', updated_at = now()
            WHERE id = $1 AND source_user_id = $2 AND status = 'awaiting_context'
            RETURNING id
            """,
            hr_contact_id,
            source_user_id,
        )
        return row is not None


async def get_hr_pending_confirm_for_user(pool: asyncpg.Pool, source_user_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT * FROM hr_contacts
            WHERE source_user_id = $1 AND status = 'pending_confirm'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            source_user_id,
        )


async def update_hr_contact_summary(
    pool: asyncpg.Pool,
    hr_contact_id: int,
    company: str | None,
    role_hint: str | None,
    vacancies_hint: str | None,
    summary: str | None,
    status: str,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE hr_contacts SET
                company = $2,
                role_hint = $3,
                vacancies_hint = $4,
                summary = $5,
                status = $6,
                updated_at = now()
            WHERE id = $1
            """,
            hr_contact_id,
            company,
            role_hint,
            vacancies_hint,
            summary,
            status,
        )


async def get_hr_contact(pool: asyncpg.Pool, hr_contact_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM hr_contacts WHERE id = $1", hr_contact_id)


async def list_file_categories(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT slug, label_ru FROM file_categories ORDER BY label_ru ASC",
        )
        return list(rows)


async def list_categories_with_counts(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.slug,
                c.label_ru,
                COALESCE(SUM(CASE WHEN f.status = 'confirmed' THEN 1 ELSE 0 END), 0)::int AS files_count,
                MAX(f.created_at) FILTER (WHERE f.status = 'confirmed') AS last_added
            FROM file_categories c
            LEFT JOIN files f ON f.confirmed_category = c.slug
            GROUP BY c.slug, c.label_ru
            ORDER BY files_count DESC, c.label_ru ASC
            """,
        )
        return list(rows)


async def ensure_file_category(
    pool: asyncpg.Pool,
    slug: str,
    label_ru: str,
    created_by: int | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO file_categories (slug, label_ru, created_by)
            VALUES ($1, $2, $3)
            ON CONFLICT (slug) DO UPDATE SET label_ru = EXCLUDED.label_ru
            """,
            slug,
            label_ru,
            created_by,
        )


async def find_active_file_by_sha256(
    pool: asyncpg.Pool, sha256_hex: str
) -> asyncpg.Record | None:
    """Файл с тем же хэшем, который ещё «жив» в системе (не удалён и не отменён)."""
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, status, original_filename, confirmed_category, suggested_category,
                   uploaded_by, created_at
            FROM files
            WHERE sha256 = $1 AND status NOT IN ('deleted', 'cancelled')
            ORDER BY id DESC
            LIMIT 1
            """,
            sha256_hex,
        )


async def insert_file_record(
    pool: asyncpg.Pool,
    storage_path: str,
    sha256: str,
    mime_type: str | None,
    uploaded_by: int,
    status: str = "processing",
    summary: str | None = None,
    suggested_category: str | None = None,
    extracted_text_preview: str | None = None,
    original_filename: str | None = None,
    subject_tags: str | None = None,
    uploader_handle: str | None = None,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO files
            (storage_path, sha256, mime_type, uploaded_by, status, summary, suggested_category,
             extracted_text_preview, original_filename, subject_tags, uploader_handle)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id
            """,
            storage_path,
            sha256,
            mime_type,
            uploaded_by,
            status,
            summary,
            suggested_category,
            extracted_text_preview,
            original_filename,
            subject_tags,
            uploader_handle,
        )
        return int(row["id"])


async def update_file_record(
    pool: asyncpg.Pool,
    file_id: int,
    *,
    status: str | None = None,
    summary: str | None = None,
    suggested_category: str | None = None,
    confirmed_category: str | None = None,
    storage_path: str | None = None,
    subject_tags: str | None = None,
    confirmed_at: Any | None = None,
) -> None:
    async with pool.acquire() as conn:
        sets: list[str] = []
        args: list[Any] = []
        idx = 1
        if status is not None:
            sets.append(f"status = ${idx}")
            args.append(status)
            idx += 1
        if summary is not None:
            sets.append(f"summary = ${idx}")
            args.append(summary)
            idx += 1
        if suggested_category is not None:
            sets.append(f"suggested_category = ${idx}")
            args.append(suggested_category)
            idx += 1
        if confirmed_category is not None:
            sets.append(f"confirmed_category = ${idx}")
            args.append(confirmed_category)
            idx += 1
        if storage_path is not None:
            sets.append(f"storage_path = ${idx}")
            args.append(storage_path)
            idx += 1
        if subject_tags is not None:
            sets.append(f"subject_tags = ${idx}")
            args.append(subject_tags)
            idx += 1
        if confirmed_at is not None:
            sets.append(f"confirmed_at = ${idx}")
            args.append(confirmed_at)
            idx += 1
        if not sets:
            return
        args.append(file_id)
        q = f"UPDATE files SET {', '.join(sets)} WHERE id = ${idx}"
        await conn.execute(q, *args)


async def get_file_record(pool: asyncpg.Pool, file_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM files WHERE id = $1", file_id)


async def list_library_files(
    pool: asyncpg.Pool,
    limit: int = 40,
    category_slug: str | None = None,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        if category_slug:
            rows = await conn.fetch(
                """
                SELECT id, storage_path, sha256, mime_type, summary, confirmed_category,
                       original_filename, uploaded_by, uploader_handle, created_at, confirmed_at, status
                FROM files
                WHERE status = 'confirmed' AND confirmed_category = $2
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
                category_slug,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT id, storage_path, sha256, mime_type, summary, confirmed_category,
                       original_filename, uploaded_by, uploader_handle, created_at, confirmed_at, status
                FROM files
                WHERE status = 'confirmed'
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit,
            )
        return list(rows)


_EVENTS_ACTIVE_FILTER = """
    status = 'published'
    AND (ends_at IS NULL OR ends_at >= now())
"""

_EVENTS_ORDER_FEED = "ORDER BY created_at DESC"


_DIGEST_FILTER = f"""
    {_EVENTS_ACTIVE_FILTER}
    AND (
        (ends_at IS NOT NULL AND ends_at <= now() + interval '14 days')
        OR (created_at >= now() - interval '7 days')
        OR (ends_at IS NULL AND created_at >= now() - interval '14 days')
    )
"""

_DIGEST_ORDER = """
    ORDER BY
        CASE
            WHEN ends_at IS NOT NULL AND ends_at > now() AND ends_at <= now() + interval '3 days' THEN 0
            WHEN ends_at IS NULL AND created_at >= now() - interval '72 hours' THEN 1
            WHEN ends_at IS NOT NULL THEN 2
            ELSE 3
        END,
        CASE
            WHEN ends_at IS NOT NULL AND ends_at > now()
            THEN extract(epoch from ends_at)
        END ASC NULLS LAST,
        created_at DESC
"""


async def list_events_feed(pool: asyncpg.Pool, limit: int = 50) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, raw_text, normalized_title, source_user_id,
                   starts_at, ends_at, ai_summary, cover_image_path, created_at
            FROM events
            WHERE {_EVENTS_ACTIVE_FILTER}
            {_EVENTS_ORDER_FEED}
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def list_events_digest(pool: asyncpg.Pool, limit: int = 30) -> list[asyncpg.Record]:
    """Выжимка: что близко по дедлайну или недавно добавлено — «прямо сейчас»."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, raw_text, normalized_title, ends_at, ai_summary, cover_image_path, created_at
            FROM events
            WHERE {_DIGEST_FILTER}
            {_DIGEST_ORDER}
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def fetch_published_event(pool: asyncpg.Pool, event_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, raw_text, normalized_title, source_user_id,
                   starts_at, ends_at, ai_summary, cover_image_path, created_at
            FROM events
            WHERE id = $1 AND status = 'published'
            """,
            event_id,
        )


async def update_event_ends_at(pool: asyncpg.Pool, event_id: int, ends_at: Any) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE events
            SET ends_at = $2
            WHERE id = $1 AND status = 'published'
            RETURNING id
            """,
            event_id,
            ends_at,
        )
        return row is not None


async def update_event_summary(pool: asyncpg.Pool, event_id: int, ai_summary: str | None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE events SET ai_summary = $2 WHERE id = $1",
            event_id,
            ai_summary,
        )


async def update_event_normalized_title(pool: asyncpg.Pool, event_id: int, title: str) -> None:
    t = (title or "").strip()
    if not t:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE events SET normalized_title = $2 WHERE id = $1 AND status = 'published'",
            event_id,
            t,
        )


async def update_event_cover_path(pool: asyncpg.Pool, event_id: int, rel_path: str) -> None:
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel or ".." in rel:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE events SET cover_image_path = $2 WHERE id = $1 AND status = 'published'",
            event_id,
            rel,
        )


async def list_events_without_summary(
    pool: asyncpg.Pool,
    limit: int = 100,
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, raw_text FROM events
            WHERE status = 'published'
              AND (ai_summary IS NULL OR ai_summary = '')
            ORDER BY created_at DESC
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def insert_web_login_code(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    code_hash: str,
    expires_at: Any,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO web_login_codes (telegram_user_id, code_hash, expires_at)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            telegram_user_id,
            code_hash,
            expires_at,
        )
        return int(row["id"])


async def fetch_valid_web_login_code(
    pool: asyncpg.Pool,
    telegram_user_id: int,
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, code_hash FROM web_login_codes
            WHERE telegram_user_id = $1
              AND expires_at > now()
              AND consumed_at IS NULL
            ORDER BY created_at DESC
            LIMIT 1
            """,
            telegram_user_id,
        )


async def consume_web_login_code(pool: asyncpg.Pool, code_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE web_login_codes SET consumed_at = now() WHERE id = $1 AND consumed_at IS NULL
            """,
            code_id,
        )


async def ensure_member_profile_row(pool: asyncpg.Pool, telegram_user_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO member_profiles (telegram_user_id) VALUES ($1)
            ON CONFLICT (telegram_user_id) DO NOTHING
            """,
            telegram_user_id,
        )


async def get_member_profile(pool: asyncpg.Pool, telegram_user_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "SELECT * FROM member_profiles WHERE telegram_user_id = $1",
            telegram_user_id,
        )


async def upsert_member_profile(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    *,
    display_name: str | None = None,
    bio: str | None = None,
    github_url: str | None = None,
    photo_paths: list[str] | None = None,
    resume_path: Any = _UNSET,
    hf_url: Any = _UNSET,
    kaggle_url: Any = _UNSET,
    leetcode_url: Any = _UNSET,
    education_institution: Any = _UNSET,
    education_year_from: Any = _UNSET,
    education_year_to: Any = _UNSET,
) -> None:
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            "SELECT 1 FROM member_profiles WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        if existing:
            sets: list[str] = []
            args: list[Any] = []
            idx = 1
            if display_name is not None:
                sets.append(f"display_name = ${idx}")
                args.append(display_name)
                idx += 1
            if bio is not None:
                sets.append(f"bio = ${idx}")
                args.append(bio)
                idx += 1
            if github_url is not None:
                sets.append(f"github_url = ${idx}")
                args.append(github_url)
                idx += 1
            if photo_paths is not None:
                sets.append(f"photo_paths = ${idx}::jsonb")
                args.append(json.dumps(photo_paths))
                idx += 1
            if resume_path is not _UNSET:
                sets.append(f"resume_path = ${idx}")
                args.append(resume_path)
                idx += 1
            if hf_url is not _UNSET:
                sets.append(f"hf_url = ${idx}")
                args.append(hf_url)
                idx += 1
            if kaggle_url is not _UNSET:
                sets.append(f"kaggle_url = ${idx}")
                args.append(kaggle_url)
                idx += 1
            if leetcode_url is not _UNSET:
                sets.append(f"leetcode_url = ${idx}")
                args.append(leetcode_url)
                idx += 1
            if education_institution is not _UNSET:
                sets.append(f"education_institution = ${idx}")
                args.append(education_institution)
                idx += 1
            if education_year_from is not _UNSET:
                sets.append(f"education_year_from = ${idx}")
                args.append(education_year_from)
                idx += 1
            if education_year_to is not _UNSET:
                sets.append(f"education_year_to = ${idx}")
                args.append(education_year_to)
                idx += 1
            if not sets:
                return
            sets.append("updated_at = now()")
            args.append(telegram_user_id)
            q = f"UPDATE member_profiles SET {', '.join(sets)} WHERE telegram_user_id = ${idx}"
            await conn.execute(q, *args)
        else:
            rp_ins = None if resume_path is _UNSET else resume_path
            hf_ins = None if hf_url is _UNSET else hf_url
            kg_ins = None if kaggle_url is _UNSET else kaggle_url
            lc_ins = None if leetcode_url is _UNSET else leetcode_url
            edu_ins = None if education_institution is _UNSET else education_institution
            yf_ins = None if education_year_from is _UNSET else education_year_from
            yt_ins = None if education_year_to is _UNSET else education_year_to
            await conn.execute(
                """
                INSERT INTO member_profiles (
                    telegram_user_id, display_name, bio, github_url, photo_paths, resume_path,
                    hf_url, kaggle_url, leetcode_url, education_institution,
                    education_year_from, education_year_to, updated_at
                )
                VALUES (
                    $1, $2, $3, COALESCE($4, 'https://github.com/'), COALESCE($5::jsonb, '[]'::jsonb), $6,
                    $7, $8, $9, $10, $11, $12, now()
                )
                """,
                telegram_user_id,
                display_name,
                bio,
                github_url if github_url is not None else "https://github.com/",
                json.dumps(photo_paths if photo_paths is not None else []),
                rp_ins,
                hf_ins,
                kg_ins,
                lc_ins,
                edu_ins,
                yf_ins,
                yt_ins,
            )


async def list_public_profiles(pool: asyncpg.Pool, limit: int = 200) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.telegram_user_id, p.display_name, p.bio, p.github_url, p.photo_paths, p.resume_path,
                   p.hf_url, p.kaggle_url, p.leetcode_url, p.education_institution,
                   p.education_year_from, p.education_year_to
            FROM member_profiles p
            INNER JOIN members m ON m.telegram_user_id = p.telegram_user_id
            WHERE m.status = 'active'
            ORDER BY p.updated_at DESC
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def log_llm_call(
    pool: asyncpg.Pool,
    purpose: str,
    model: str | None,
    prompt_tokens: int | None,
    completion_tokens: int | None,
    latency_ms: int | None,
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO llm_calls (purpose, model, prompt_tokens, completion_tokens, latency_ms)
            VALUES ($1, $2, $3, $4, $5)
            """,
            purpose,
            model,
            prompt_tokens,
            completion_tokens,
            latency_ms,
        )


async def search_events_ilike(pool: asyncpg.Pool, needle: str, limit: int = 5) -> list[str]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT raw_text FROM events
            WHERE raw_text ILIKE $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            f"%{needle}%",
            limit,
        )
        return [r["raw_text"] for r in rows]


async def add_activity_points(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    points: int,
    reason: str,
    meta: dict[str, Any] | None,
) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                UPDATE members SET activity_points = activity_points + $2
                WHERE telegram_user_id = $1
                RETURNING activity_points
                """,
                telegram_user_id,
                points,
            )
            if not row:
                return 0
            await conn.execute(
                """
                INSERT INTO activity_ledger (telegram_user_id, reason, points, meta)
                VALUES ($1, $2, $3, $4::jsonb)
                """,
                telegram_user_id,
                reason,
                points,
                json.dumps(meta or {}),
            )
            return int(row["activity_points"])


async def get_member_activity_points(pool: asyncpg.Pool, telegram_user_id: int) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT activity_points FROM members WHERE telegram_user_id = $1",
            telegram_user_id,
        )
        return int(row["activity_points"]) if row else 0


async def count_activity_reason_since(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    reason: str,
    since,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT COUNT(*)::int AS n FROM activity_ledger
            WHERE telegram_user_id = $1 AND reason = $2 AND created_at >= $3
            """,
            telegram_user_id,
            reason,
            since,
        )
        return int(row["n"]) if row else 0


async def mark_library_file_deleted(pool: asyncpg.Pool, file_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            UPDATE files
            SET status = 'deleted'
            WHERE id = $1 AND status = 'confirmed'
            RETURNING id, storage_path, confirmed_category
            """,
            file_id,
        )


async def fetch_event_for_admin(pool: asyncpg.Pool, event_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT id, status, published_message_id
            FROM events
            WHERE id = $1
            """,
            event_id,
        )


async def hide_published_event(pool: asyncpg.Pool, event_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE events
            SET status = 'hidden'
            WHERE id = $1 AND status = 'published'
            RETURNING id
            """,
            event_id,
        )
        return row is not None


async def event_reaction_counts(pool: asyncpg.Pool, event_ids: list[int]) -> list[asyncpg.Record]:
    if not event_ids:
        return []
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                SELECT event_id, emoji, COUNT(*)::int AS n
                FROM event_reactions
                WHERE event_id = ANY($1::bigint[])
                GROUP BY event_id, emoji
                ORDER BY event_id, n DESC, emoji
                """,
                event_ids,
            )
        )


async def event_user_reactions_map(
    pool: asyncpg.Pool, telegram_user_id: int, event_ids: list[int]
) -> dict[int, str]:
    if not event_ids:
        return {}
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_id, emoji
            FROM event_reactions
            WHERE telegram_user_id = $1 AND event_id = ANY($2::bigint[])
            """,
            telegram_user_id,
            event_ids,
        )
    return {int(r["event_id"]): str(r["emoji"]) for r in rows}


async def upsert_event_reaction(
    pool: asyncpg.Pool, event_id: int, telegram_user_id: int, emoji: str
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO event_reactions (event_id, telegram_user_id, emoji)
            VALUES ($1, $2, $3)
            ON CONFLICT (event_id, telegram_user_id)
            DO UPDATE SET emoji = EXCLUDED.emoji, created_at = now()
            """,
            event_id,
            telegram_user_id,
            emoji,
        )


async def delete_event_reaction(pool: asyncpg.Pool, event_id: int, telegram_user_id: int) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "DELETE FROM event_reactions WHERE event_id = $1 AND telegram_user_id = $2",
            event_id,
            telegram_user_id,
        )


async def list_event_comments_limited(
    pool: asyncpg.Pool, event_ids: list[int], per_event: int = 12
) -> list[asyncpg.Record]:
    if not event_ids:
        return []
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                WITH ranked AS (
                    SELECT
                        c.id,
                        c.event_id,
                        c.author_telegram_id,
                        c.body,
                        c.created_at,
                        mp.display_name AS author_display_name,
                        ROW_NUMBER() OVER (
                            PARTITION BY c.event_id ORDER BY c.created_at ASC
                        ) AS rn
                    FROM event_comments c
                    LEFT JOIN member_profiles mp
                        ON mp.telegram_user_id = c.author_telegram_id
                    WHERE c.event_id = ANY($1::bigint[])
                )
                SELECT id, event_id, author_telegram_id, body, created_at, author_display_name
                FROM ranked
                WHERE rn <= $2
                ORDER BY event_id, created_at ASC
                """,
                event_ids,
                per_event,
            )
        )


async def insert_event_comment(
    pool: asyncpg.Pool, event_id: int, author_telegram_id: int, body: str
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO event_comments (event_id, author_telegram_id, body)
            VALUES ($1, $2, $3)
            RETURNING id
            """,
            event_id,
            author_telegram_id,
            body,
        )
        return int(row["id"])


async def fetch_event_comment(pool: asyncpg.Pool, comment_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM event_comments WHERE id = $1", comment_id)


async def delete_event_comment(pool: asyncpg.Pool, comment_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            "DELETE FROM event_comments WHERE id = $1 RETURNING id, event_id, author_telegram_id",
            comment_id,
        )


async def create_hackathon_team(
    pool: asyncpg.Pool,
    *,
    title: str,
    description: str,
    starts_at: Any | None,
    ends_at: Any | None,
    max_members: int,
    creator_telegram_id: int,
) -> int:
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                INSERT INTO hackathon_teams (
                    title, description, starts_at, ends_at, max_members, creator_telegram_id
                )
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING id
                """,
                title,
                description,
                starts_at,
                ends_at,
                max_members,
                creator_telegram_id,
            )
            tid = int(row["id"])
            await conn.execute(
                """
                INSERT INTO hackathon_team_members (team_id, telegram_user_id, role)
                VALUES ($1, $2, 'creator')
                """,
                tid,
                creator_telegram_id,
            )
            return tid


async def list_hackathon_teams(pool: asyncpg.Pool, limit: int = 100) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                SELECT
                    t.*,
                    (
                        SELECT COUNT(*)::int FROM hackathon_team_members m
                        WHERE m.team_id = t.id
                    ) AS member_count
                FROM hackathon_teams t
                WHERE t.status = 'open'
                ORDER BY t.created_at DESC
                LIMIT $1
                """,
                limit,
            )
        )


async def get_hackathon_team(pool: asyncpg.Pool, team_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT
                t.*,
                (
                    SELECT COUNT(*)::int FROM hackathon_team_members m
                    WHERE m.team_id = t.id
                ) AS member_count
            FROM hackathon_teams t
            WHERE t.id = $1
            """,
            team_id,
        )


async def list_hackathon_team_members(pool: asyncpg.Pool, team_id: int) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                SELECT
                    m.telegram_user_id,
                    m.role,
                    m.joined_at,
                    mp.display_name,
                    mp.photo_paths
                FROM hackathon_team_members m
                LEFT JOIN member_profiles mp ON mp.telegram_user_id = m.telegram_user_id
                WHERE m.team_id = $1
                ORDER BY
                    CASE WHEN m.role = 'creator' THEN 0 ELSE 1 END,
                    m.joined_at ASC
                """,
                team_id,
            )
        )


async def is_hackathon_team_member(pool: asyncpg.Pool, team_id: int, telegram_user_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT 1 FROM hackathon_team_members
            WHERE team_id = $1 AND telegram_user_id = $2
            """,
            team_id,
            telegram_user_id,
        )
        return row is not None


async def get_hackathon_application(
    pool: asyncpg.Pool, team_id: int, applicant_telegram_id: int
) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT * FROM hackathon_applications
            WHERE team_id = $1 AND applicant_telegram_id = $2
            """,
            team_id,
            applicant_telegram_id,
        )


async def list_hackathon_pending_applications(
    pool: asyncpg.Pool, team_id: int
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        return list(
            await conn.fetch(
                """
                SELECT
                    a.id,
                    a.applicant_telegram_id,
                    a.created_at,
                    mp.display_name,
                    mp.photo_paths
                FROM hackathon_applications a
                LEFT JOIN member_profiles mp ON mp.telegram_user_id = a.applicant_telegram_id
                WHERE a.team_id = $1 AND a.status = 'pending'
                ORDER BY a.created_at ASC
                """,
                team_id,
            )
        )


async def apply_hackathon_team(
    pool: asyncpg.Pool, team_id: int, applicant_telegram_id: int
) -> str:
    """Возвращает: ok | full | closed | already_member | already_pending | team_missing."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            team = await conn.fetchrow(
                "SELECT id, max_members, status FROM hackathon_teams WHERE id = $1 FOR UPDATE",
                team_id,
            )
            if not team:
                return "team_missing"
            if str(team["status"]) != "open":
                return "closed"
            n = await conn.fetchval(
                "SELECT COUNT(*)::int FROM hackathon_team_members WHERE team_id = $1",
                team_id,
            )
            if int(n) >= int(team["max_members"]):
                return "full"
            mem = await conn.fetchrow(
                """
                SELECT 1 FROM hackathon_team_members
                WHERE team_id = $1 AND telegram_user_id = $2
                """,
                team_id,
                applicant_telegram_id,
            )
            if mem:
                return "already_member"
            existing = await conn.fetchrow(
                """
                SELECT status FROM hackathon_applications
                WHERE team_id = $1 AND applicant_telegram_id = $2
                """,
                team_id,
                applicant_telegram_id,
            )
            if existing:
                st = str(existing["status"])
                if st == "pending":
                    return "already_pending"
                if st == "accepted":
                    return "already_member"
                if st == "rejected":
                    await conn.execute(
                        """
                        UPDATE hackathon_applications
                        SET status = 'pending', created_at = now()
                        WHERE team_id = $1 AND applicant_telegram_id = $2
                        """,
                        team_id,
                        applicant_telegram_id,
                    )
                    return "ok"
            await conn.execute(
                """
                INSERT INTO hackathon_applications (team_id, applicant_telegram_id, status)
                VALUES ($1, $2, 'pending')
                """,
                team_id,
                applicant_telegram_id,
            )
            return "ok"


async def hackathon_accept_application(
    pool: asyncpg.Pool, application_id: int, acting_creator_id: int
) -> str:
    """ok | forbidden | not_pending | full | missing."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            app = await conn.fetchrow(
                "SELECT * FROM hackathon_applications WHERE id = $1 FOR UPDATE",
                application_id,
            )
            if not app:
                return "missing"
            if str(app["status"]) != "pending":
                return "not_pending"
            team_id = int(app["team_id"])
            team = await conn.fetchrow(
                "SELECT * FROM hackathon_teams WHERE id = $1 FOR UPDATE",
                team_id,
            )
            if not team or int(team["creator_telegram_id"]) != acting_creator_id:
                return "forbidden"
            n = await conn.fetchval(
                "SELECT COUNT(*)::int FROM hackathon_team_members WHERE team_id = $1",
                team_id,
            )
            if int(n) >= int(team["max_members"]):
                return "full"
            applicant = int(app["applicant_telegram_id"])
            await conn.execute(
                """
                INSERT INTO hackathon_team_members (team_id, telegram_user_id, role)
                VALUES ($1, $2, 'member')
                ON CONFLICT (team_id, telegram_user_id) DO NOTHING
                """,
                team_id,
                applicant,
            )
            await conn.execute(
                "UPDATE hackathon_applications SET status = 'accepted' WHERE id = $1",
                application_id,
            )
            return "ok"


async def hackathon_reject_application(
    pool: asyncpg.Pool, application_id: int, acting_creator_id: int
) -> str:
    async with pool.acquire() as conn:
        async with conn.transaction():
            app = await conn.fetchrow(
                "SELECT * FROM hackathon_applications WHERE id = $1 FOR UPDATE",
                application_id,
            )
            if not app:
                return "missing"
            if str(app["status"]) != "pending":
                return "not_pending"
            team = await conn.fetchrow(
                "SELECT creator_telegram_id FROM hackathon_teams WHERE id = $1",
                int(app["team_id"]),
            )
            if not team or int(team["creator_telegram_id"]) != acting_creator_id:
                return "forbidden"
            await conn.execute(
                "UPDATE hackathon_applications SET status = 'rejected' WHERE id = $1",
                application_id,
            )
            return "ok"


async def company_slug_taken(pool: asyncpg.Pool, slug: str) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT 1 FROM companies WHERE slug = $1", slug)
        return row is not None


async def allocate_unique_company_slug(pool: asyncpg.Pool, display_name: str) -> str:
    from utils.company_slug import slugify_company_name

    base = slugify_company_name(display_name)
    for i in range(0, 500):
        slug = base if i == 0 else f"{base}-{i}"
        if not await company_slug_taken(pool, slug):
            return slug
    import secrets

    return f"{base}-{secrets.token_hex(3)}"


async def find_company_id_by_name_ci(pool: asyncpg.Pool, name: str) -> int | None:
    n = (name or "").strip()
    if len(n) < 2:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id FROM companies
            WHERE lower(trim(name)) = lower(trim($1))
            LIMIT 1
            """,
            n,
        )
        return int(row["id"]) if row else None


async def list_companies_compact(pool: asyncpg.Pool, limit: int = 40) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, name, slug FROM companies
            ORDER BY name ASC
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def list_recent_confirmed_files_for_uploader(
    pool: asyncpg.Pool, telegram_user_id: int, limit: int = 50
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, original_filename, summary, confirmed_category, created_at
            FROM files
            WHERE uploaded_by = $1 AND status = 'confirmed'
            ORDER BY confirmed_at DESC NULLS LAST, created_at DESC
            LIMIT $2
            """,
            telegram_user_id,
            limit,
        )
        return list(rows)


async def insert_company(
    pool: asyncpg.Pool,
    slug: str,
    name: str,
    description: str | None,
    created_by: int,
    photo_paths: list[str] | None = None,
) -> int:
    paths = photo_paths if photo_paths else []
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO companies (slug, name, description, photo_paths, created_by)
            VALUES ($1, $2, $3, $4::jsonb, $5)
            RETURNING id
            """,
            slug,
            name,
            description,
            json.dumps(paths),
            created_by,
        )
        return int(row["id"])


async def update_company_photo_paths(
    pool: asyncpg.Pool, company_id: int, photo_paths: list[str]
) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """
            UPDATE companies SET photo_paths = $2::jsonb, updated_at = now() WHERE id = $1
            """,
            company_id,
            json.dumps(photo_paths),
        )


async def get_company_by_slug(pool: asyncpg.Pool, slug: str) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM companies WHERE slug = $1", slug)


async def get_company_by_id(pool: asyncpg.Pool, company_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM companies WHERE id = $1", company_id)


async def get_company_tab_counts(pool: asyncpg.Pool, company_id: int) -> asyncpg.Record:
    async with pool.acquire() as conn:
        return await conn.fetchrow(
            """
            SELECT
                (SELECT COUNT(*)::int FROM hr_contacts
                 WHERE company_id = $1 AND status = 'confirmed') AS hr_n,
                (SELECT COUNT(*)::int FROM company_interview_reviews
                 WHERE company_id = $1) AS reviews_n,
                (SELECT COUNT(*)::int FROM company_files WHERE company_id = $1) AS files_n
            """,
            company_id,
        )


async def delete_company(pool: asyncpg.Pool, company_id: int) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "DELETE FROM companies WHERE id = $1 RETURNING id",
            company_id,
        )
        return row is not None


async def list_companies_with_counts(pool: asyncpg.Pool) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                c.*,
                COALESCE((
                    SELECT COUNT(*)::int FROM hr_contacts h
                    WHERE h.company_id = c.id AND h.status = 'confirmed'
                ), 0) AS hr_count,
                COALESCE((
                    SELECT COUNT(*)::int FROM company_files cf WHERE cf.company_id = c.id
                ), 0) AS files_count,
                COALESCE((
                    SELECT COUNT(*)::int FROM company_interview_reviews r
                    WHERE r.company_id = c.id
                ), 0) AS reviews_count
            FROM companies c
            ORDER BY c.updated_at DESC, c.name ASC
            """
        )
        return list(rows)


async def list_hr_for_company(pool: asyncpg.Pool, company_id: int) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, contact_ref, company, role_hint, vacancies_hint, summary, updated_at
            FROM hr_contacts
            WHERE company_id = $1 AND status = 'confirmed'
            ORDER BY updated_at DESC
            """,
            company_id,
        )
        return list(rows)


async def list_hr_contacts_for_company_picker(
    pool: asyncpg.Pool, company_id: int, limit: int = 400
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT id, contact_ref, company, role_hint, summary
            FROM hr_contacts
            WHERE status = 'confirmed'
              AND (company_id IS NULL OR company_id = $2)
            ORDER BY updated_at DESC
            LIMIT $1
            """,
            limit,
            company_id,
        )
        return list(rows)


async def set_hr_contact_company(
    pool: asyncpg.Pool,
    hr_contact_id: int,
    company_id: int,
) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE hr_contacts SET company_id = $2, updated_at = now()
            WHERE id = $1 AND status = 'confirmed'
              AND (company_id IS NULL OR company_id = $2)
            RETURNING id
            """,
            hr_contact_id,
            company_id,
        )
        if row:
            await conn.execute(
                "UPDATE companies SET updated_at = now() WHERE id = $1",
                company_id,
            )
        return row is not None


async def unlink_hr_contact_from_company(
    pool: asyncpg.Pool, hr_contact_id: int, company_id: int
) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            UPDATE hr_contacts SET company_id = NULL, updated_at = now()
            WHERE id = $1 AND company_id = $2 AND status = 'confirmed'
            RETURNING id
            """,
            hr_contact_id,
            company_id,
        )
        if row:
            await conn.execute(
                "UPDATE companies SET updated_at = now() WHERE id = $1",
                company_id,
            )
        return row is not None


async def link_company_file(
    pool: asyncpg.Pool,
    company_id: int,
    file_id: int,
    linked_by: int,
    note: str | None,
) -> str:
    async with pool.acquire() as conn:
        c = await conn.fetchrow("SELECT id FROM companies WHERE id = $1", company_id)
        if not c:
            return "missing_company"
        f = await conn.fetchrow(
            "SELECT id FROM files WHERE id = $1 AND status = 'confirmed'",
            file_id,
        )
        if not f:
            return "bad_file"
        try:
            await conn.execute(
                """
                INSERT INTO company_files (company_id, file_id, linked_by, note)
                VALUES ($1, $2, $3, $4)
                """,
                company_id,
                file_id,
                linked_by,
                note,
            )
        except UniqueViolationError:
            return "duplicate"
        await conn.execute(
            "UPDATE companies SET updated_at = now() WHERE id = $1",
            company_id,
        )
        return "ok"


async def list_company_files_with_meta(
    pool: asyncpg.Pool, company_id: int
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT cf.id AS link_id, cf.note, cf.created_at AS linked_at,
                   f.id AS file_id, f.original_filename, f.mime_type, f.summary,
                   f.confirmed_category
            FROM company_files cf
            JOIN files f ON f.id = cf.file_id
            WHERE cf.company_id = $1
            ORDER BY cf.created_at DESC
            """,
            company_id,
        )
        return list(rows)


async def insert_company_interview_review(
    pool: asyncpg.Pool,
    company_id: int,
    author_telegram_id: int,
    body: str,
    hr_contact_id: int | None,
) -> str:
    b = (body or "").strip()
    if hr_contact_id is None and len(b) < 10:
        return "short_body"
    async with pool.acquire() as conn:
        if hr_contact_id is not None:
            hr = await conn.fetchrow(
                """
                SELECT id, company_id, status FROM hr_contacts WHERE id = $1
                """,
                hr_contact_id,
            )
            if not hr or str(hr["status"]) != "confirmed":
                return "bad_hr"
            cid = hr["company_id"]
            if cid is not None and int(cid) != company_id:
                return "hr_other_company"
        try:
            await conn.execute(
                """
                INSERT INTO company_interview_reviews
                    (company_id, author_telegram_id, body, hr_contact_id)
                VALUES ($1, $2, $3, $4)
                """,
                company_id,
                author_telegram_id,
                b or "—",
                hr_contact_id,
            )
        except CheckViolationError:
            return "short_body"
        await conn.execute(
            "UPDATE companies SET updated_at = now() WHERE id = $1",
            company_id,
        )
        return "ok"


async def list_company_interview_reviews(
    pool: asyncpg.Pool, company_id: int
) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.id, r.body, r.created_at, r.author_telegram_id,
                   r.hr_contact_id,
                   h.summary AS hr_summary,
                   h.role_hint AS hr_role,
                   h.contact_ref AS hr_contact_ref
            FROM company_interview_reviews r
            LEFT JOIN hr_contacts h ON h.id = r.hr_contact_id
            WHERE r.company_id = $1
            ORDER BY r.created_at DESC
            """,
            company_id,
        )
        return list(rows)
