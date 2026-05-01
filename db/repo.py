from __future__ import annotations

import json
from typing import Any

import asyncpg


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
    AND (ends_at IS NULL OR ends_at >= date_trunc('day', now() AT TIME ZONE 'UTC'))
"""

_EVENTS_ORDER = """
    ORDER BY
        CASE
            WHEN ends_at IS NOT NULL AND ends_at > now() AND ends_at <= now() + interval '7 days' THEN 0
            WHEN created_at >= now() - interval '48 hours' THEN 1
            ELSE 2
        END,
        CASE
            WHEN ends_at IS NOT NULL AND ends_at > now() AND ends_at <= now() + interval '7 days'
            THEN extract(epoch from ends_at)
        END ASC NULLS LAST,
        created_at DESC
"""


async def list_events_feed(pool: asyncpg.Pool, limit: int = 50) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, raw_text, normalized_title, source_user_id,
                   starts_at, ends_at, ai_summary, created_at
            FROM events
            WHERE {_EVENTS_ACTIVE_FILTER}
            {_EVENTS_ORDER}
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def list_events_today_strip(pool: asyncpg.Pool, limit: int = 20) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT id, raw_text, normalized_title, ends_at, ai_summary, created_at
            FROM events
            WHERE {_EVENTS_ACTIVE_FILTER}
            {_EVENTS_ORDER}
            LIMIT $1
            """,
            limit,
        )
        return list(rows)


async def update_event_summary(pool: asyncpg.Pool, event_id: int, ai_summary: str | None) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE events SET ai_summary = $2 WHERE id = $1",
            event_id,
            ai_summary,
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
            if not sets:
                return
            sets.append("updated_at = now()")
            args.append(telegram_user_id)
            q = f"UPDATE member_profiles SET {', '.join(sets)} WHERE telegram_user_id = ${idx}"
            await conn.execute(q, *args)
        else:
            await conn.execute(
                """
                INSERT INTO member_profiles (
                    telegram_user_id, display_name, bio, github_url, photo_paths, updated_at
                )
                VALUES ($1, $2, $3, COALESCE($4, 'https://github.com/'), COALESCE($5::jsonb, '[]'::jsonb), now())
                """,
                telegram_user_id,
                display_name,
                bio,
                github_url if github_url is not None else "https://github.com/",
                json.dumps(photo_paths if photo_paths is not None else []),
            )


async def list_public_profiles(pool: asyncpg.Pool, limit: int = 200) -> list[asyncpg.Record]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT p.telegram_user_id, p.display_name, p.bio, p.github_url, p.photo_paths
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
