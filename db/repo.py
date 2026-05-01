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
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO events (raw_text, normalized_title, source_user_id, status, published_message_id)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING id
            """,
            raw_text,
            normalized_title,
            source_user_id,
            status,
            published_message_id,
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
    telegram_uid: int,
    source_user_id: int,
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO hr_contacts (telegram_uid, status, source_user_id)
            VALUES ($1, 'awaiting_context', $2)
            RETURNING id
            """,
            telegram_uid,
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
) -> int:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO files
            (storage_path, sha256, mime_type, uploaded_by, status, summary, suggested_category, extracted_text_preview)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
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
        if not sets:
            return
        args.append(file_id)
        q = f"UPDATE files SET {', '.join(sets)} WHERE id = ${idx}"
        await conn.execute(q, *args)


async def get_file_record(pool: asyncpg.Pool, file_id: int) -> asyncpg.Record | None:
    async with pool.acquire() as conn:
        return await conn.fetchrow("SELECT * FROM files WHERE id = $1", file_id)


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
