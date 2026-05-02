from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from config import get_settings
from db import repo

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text(encoding="utf-8")


def _mistral_bearer(*, prefer_site_key: bool) -> str:
    settings = get_settings()
    if prefer_site_key:
        alt = (settings.mistral_api_key_for_site or "").strip()
        if alt:
            return alt
    return settings.mistral_api_key


async def mistral_chat(
    pool,
    *,
    purpose: str,
    model: str,
    system: str,
    user: str,
    json_mode: bool = False,
    prefer_site_key: bool = False,
) -> tuple[str, int | None, int | None]:
    settings = get_settings()
    api_key = _mistral_bearer(prefer_site_key=prefer_site_key)
    url = "https://api.mistral.ai/v1/chat/completions"
    body: dict = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
        )
        r.raise_for_status()
        data = r.json()

    latency_ms = int((time.perf_counter() - t0) * 1000)
    usage = data.get("usage") or {}
    pt = usage.get("prompt_tokens")
    ct = usage.get("completion_tokens")
    content = data["choices"][0]["message"]["content"]
    await repo.log_llm_call(pool, purpose, model, pt, ct, latency_ms)
    return content, pt, ct


async def assess_event_clarity(pool, user_text: str) -> dict:
    """Оценка, достаточно ли ясен текст анонса. При сбое ИИ — считаем, что можно публиковать."""
    settings = get_settings()
    raw_t = (user_text or "").strip()
    if len(raw_t) < 8:
        return {
            "clear_enough": False,
            "hint_ru": "Сообщение очень короткое — добавь, что именно анонсируешь (и даты/ссылку, если есть).",
        }
    template = _load_prompt("event_clarity.txt")
    user_block = template.replace("{user_text}", raw_t[:8000])
    try:
        raw, _, _ = await mistral_chat(
            pool,
            purpose="event_clarity",
            model=settings.mistral_model_routing,
            system="Reply with JSON only.",
            user=user_block,
            json_mode=True,
        )
        data = json.loads(raw)
        ce = bool(data.get("clear_enough", True))
        hint = str(data.get("hint_ru") or "").strip()
        return {"clear_enough": ce, "hint_ru": hint}
    except Exception:
        return {"clear_enough": True, "hint_ru": ""}


async def classify_intent(pool, user_text: str) -> tuple[str, float]:
    settings = get_settings()
    template = _load_prompt("classify.txt")
    user_block = template.replace("{user_text}", user_text[:8000])
    raw, _, _ = await mistral_chat(
        pool,
        purpose="classify_intent",
        model=settings.mistral_model_routing,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
    )
    data = json.loads(raw)
    intent = str(data.get("intent", "other"))
    conf = float(data.get("confidence", 0))
    if intent not in ("event", "hr_contact", "file_material", "other"):
        intent = "other"
    return intent, conf


async def summarize_event(pool, raw_text: str) -> dict:
    """Короткое саммари одного мероприятия для ленты сайта."""
    settings = get_settings()
    template = _load_prompt("event_summary.txt")
    user_block = template.replace("{raw_text}", raw_text[:8000])
    raw, _, _ = await mistral_chat(
        pool,
        purpose="event_summary",
        model=settings.mistral_model_default,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
        prefer_site_key=True,
    )
    return json.loads(raw)


async def dedup_event(pool, new_text: str, existing_texts: list[str]) -> dict:
    settings = get_settings()
    template = _load_prompt("event_dedup.txt")
    existing_block = "\n---\n".join(existing_texts[:20]) if existing_texts else "(none)"
    user_block = template.replace("{new_text}", new_text[:12000]).replace("{existing_block}", existing_block)
    raw, _, _ = await mistral_chat(
        pool,
        purpose="event_dedup",
        model=settings.mistral_model_default,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
        prefer_site_key=True,
    )
    return json.loads(raw)


async def extract_hr(pool, contact_identifier: str, context_lines: list[str]) -> dict:
    from services import hr_context_config

    settings = get_settings()
    template = _load_prompt("hr_extract.txt")
    ctx = "\n".join(context_lines[-40:])
    el, vn, note = hr_context_config.prompt_blocks()
    user_block = (
        template.replace("{contact_identifier}", str(contact_identifier))
        .replace("{context_block}", ctx[:12000])
        .replace("{employers_list_block}", el)
        .replace("{venues_never_company_block}", vn)
        .replace("{notes_ru}", note)
    )
    raw, _, _ = await mistral_chat(
        pool,
        purpose="hr_extract",
        model=settings.mistral_model_default,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
    )
    return json.loads(raw)


async def extract_interview_story(pool, raw_text: str) -> dict:
    settings = get_settings()
    template = _load_prompt("interview_extract.txt")
    user_block = template.replace("{raw_text}", raw_text[:12000])
    raw, _, _ = await mistral_chat(
        pool,
        purpose="interview_extract",
        model=settings.mistral_model_default,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
    )
    return json.loads(raw)


async def interview_confirmation_preview(pool, data: dict, had_voice: bool) -> dict:
    """Короткий текст для подтверждения пользователем перед сохранением собеса."""
    settings = get_settings()
    template = _load_prompt("interview_preview_user.txt")
    blob = json.dumps(data, ensure_ascii=False)[:6000]
    user_block = template.replace("{extract_json}", blob).replace(
        "{had_voice}", "да" if had_voice else "нет"
    )
    raw, _, _ = await mistral_chat(
        pool,
        purpose="interview_preview_user",
        model=settings.mistral_model_routing,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
        prefer_site_key=True,
    )
    out = json.loads(raw)
    return {
        "preview_ru": str(out.get("preview_ru") or "").strip(),
        "summary_line": str(out.get("summary_line") or "").strip(),
    }


async def match_company_for_hub(
    pool,
    *,
    mode: str,
    companies_block: str,
    hint: str,
    detail: str,
) -> dict:
    """Сопоставление с карточкой компании на сайте или решение создать новую. JSON от промпта company_match."""
    settings = get_settings()
    template = _load_prompt("company_match.txt")
    user_block = (
        template.replace("{companies_block}", companies_block[:12000])
        .replace("{mode}", mode[:32])
        .replace("{hint}", (hint or "")[:500])
        .replace("{detail}", (detail or "")[:8000])
    )
    raw, _, _ = await mistral_chat(
        pool,
        purpose="company_match",
        model=settings.mistral_model_routing,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
        prefer_site_key=True,
    )
    data = json.loads(raw)
    act = str(data.get("action") or "skip").lower()
    if act not in ("match", "create", "skip"):
        act = "skip"
    cid = data.get("company_id")
    try:
        company_id = int(cid) if cid is not None else None
    except (TypeError, ValueError):
        company_id = None
    name = str(data.get("new_company_name_ru") or "").strip() or None
    return {
        "action": act,
        "company_id": company_id,
        "new_company_name_ru": name,
        "reason": str(data.get("reason") or "").strip(),
    }


async def summarize_file(pool, text_sample: str, categories_block: str) -> dict:
    settings = get_settings()
    template = _load_prompt("file_summarize.txt")
    user_block = template.replace("{categories_block}", categories_block).replace(
        "{text_sample}", text_sample[:14000]
    )
    raw, _, _ = await mistral_chat(
        pool,
        purpose="file_summarize",
        model=settings.mistral_model_default,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
    )
    return json.loads(raw)


async def voice_gist(pool, transcript: str) -> dict:
    settings = get_settings()
    template = _load_prompt("voice_context.txt")
    user_block = template.replace("{transcript}", transcript[:8000])
    raw, _, _ = await mistral_chat(
        pool,
        purpose="voice_gist",
        model=settings.mistral_model_routing,
        system="Reply with JSON only.",
        user=user_block,
        json_mode=True,
    )
    return json.loads(raw)
