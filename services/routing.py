from __future__ import annotations

import re

from services import llm

_UID_SINGLE = re.compile(r"^\s*(\d{6,12})\s*$")


def heuristic_route(text: str | None, has_document: bool, mime: str | None) -> str | None:
    if has_document:
        return "file_material"
    if text and _UID_SINGLE.match(text.strip()):
        return "hr_contact"
    return None


async def route_intent(pool, text: str | None, has_document: bool, mime: str | None) -> str:
    h = heuristic_route(text, has_document, mime)
    if h:
        return h
    if not text or not str(text).strip():
        return "other"
    intent, conf = await llm.classify_intent(pool, str(text))
    if conf < 0.35:
        return "other"
    return intent
