"""Agent router: heuristic shortcuts + LLM classify (mocked)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services import routing


@pytest.mark.parametrize(
    ("text", "has_doc", "mime", "expected"),
    [
        (None, True, "application/pdf", "file_material"),
        ("@recruiter_name", False, None, "hr_contact"),
        ("79991234567", False, None, "hr_contact"),
        ("Привет!", False, None, None),
    ],
)
def test_heuristic_route(text, has_doc, mime, expected) -> None:
    assert routing.heuristic_route(text, has_doc, mime) == expected


def test_extract_url_finds_https() -> None:
    assert routing.extract_url("Смотри https://example.com/docs и пиши") == "https://example.com/docs"


def test_extract_url_none_when_missing() -> None:
    assert routing.extract_url("только текст без ссылки") is None


@pytest.mark.asyncio
async def test_route_intent_skips_llm_when_heuristic_matches() -> None:
    with patch("services.routing.llm.classify_intent", new=AsyncMock()) as mock_llm:
        intent = await routing.route_intent(None, "@hr_bot", False, None)
    assert intent == "hr_contact"
    mock_llm.assert_not_called()


@pytest.mark.asyncio
async def test_route_intent_uses_llm_when_no_heuristic() -> None:
    with patch(
        "services.routing.llm.classify_intent",
        new=AsyncMock(return_value=("event", 0.91)),
    ):
        intent = await routing.route_intent(None, "Хакатон 12 апреля", False, None)
    assert intent == "event"


@pytest.mark.asyncio
async def test_route_intent_low_confidence_falls_back_to_other() -> None:
    with patch(
        "services.routing.llm.classify_intent",
        new=AsyncMock(return_value=("event", 0.1)),
    ):
        intent = await routing.route_intent(None, "может быть митап", False, None)
    assert intent == "other"


@pytest.mark.asyncio
async def test_route_intent_empty_text_without_attachment() -> None:
    with patch("services.routing.llm.classify_intent", new=AsyncMock()) as mock_llm:
        intent = await routing.route_intent(None, "   ", False, None)
    assert intent == "other"
    mock_llm.assert_not_called()
