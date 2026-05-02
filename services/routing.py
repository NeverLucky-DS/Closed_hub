from __future__ import annotations

from services import hr_service, llm


def heuristic_route(text: str | None, has_document: bool, mime: str | None) -> str | None:
    if has_document:
        return "file_material"
    if text and hr_service.try_parse_hr_contact_line(text.strip()):
        return "hr_contact"
    return None


async def route_intent(pool, text: str | None, has_document: bool, mime: str | None) -> str:
    h = heuristic_route(text, has_document, mime)
    if h:
        return h
    if not text or not str(text).strip():
        return "other"
    intent, conf = await llm.classify_intent(pool, str(text))
    if conf < 0.28:
        return "other"
    return intent
