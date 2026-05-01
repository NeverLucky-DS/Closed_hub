from __future__ import annotations

import logging

import httpx

from config import get_settings

log = logging.getLogger(__name__)

GROQ_TRANSCRIBE_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
WHISPER_MODEL = "whisper-large-v3-turbo"


async def transcribe_ogg_opus(audio_bytes: bytes, filename: str = "voice.ogg") -> str:
    settings = get_settings()
    if not settings.groq_api_key:
        raise RuntimeError("GROQ_API_KEY не задан в .env")

    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            GROQ_TRANSCRIBE_URL,
            headers={"Authorization": f"Bearer {settings.groq_api_key}"},
            files={"file": (filename, audio_bytes, "audio/ogg")},
            data={"model": WHISPER_MODEL, "temperature": "0"},
        )
        if r.status_code >= 400:
            log.warning("groq transcribe http %s: %s", r.status_code, r.text[:500])
        r.raise_for_status()
        data = r.json()
        return str(data.get("text") or "").strip()
