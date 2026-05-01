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


async def mistral_chat(
    pool,
    *,
    purpose: str,
    model: str,
    system: str,
    user: str,
    json_mode: bool = False,
) -> tuple[str, int | None, int | None]:
    settings = get_settings()
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
            headers={"Authorization": f"Bearer {settings.mistral_api_key}", "Content-Type": "application/json"},
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
    )
    return json.loads(raw)


async def extract_hr(pool, telegram_uid: int, context_lines: list[str]) -> dict:
    settings = get_settings()
    template = _load_prompt("hr_extract.txt")
    ctx = "\n".join(context_lines[-40:])
    user_block = (
        template.replace("{telegram_uid}", str(telegram_uid)).replace("{context_block}", ctx[:12000])
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
