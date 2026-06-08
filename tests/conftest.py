"""Pytest fixtures: env for imports, mocked DB pool for FastAPI lifespan."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tests.mock_pool import mock_pool

# Required before `config.get_settings()` / `web.app` import.
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "pytest-token-not-real")
os.environ.setdefault("MISTRAL_API_KEY", "pytest-mistral-not-real")
os.environ.setdefault("WEB_SESSION_SECRET", "pytest-session-secret-min-32-chars!!")


@pytest.fixture
async def app_client() -> AsyncIterator[AsyncClient]:
    from config import get_settings

    get_settings.cache_clear()
    pool = mock_pool()
    patches = (
        patch("web.app.create_pool", new=AsyncMock(return_value=pool)),
        patch("web.app.apply_pending_patches", new=AsyncMock()),
        patch("web.app.close_pool", new=AsyncMock()),
    )
    for p in patches:
        p.start()
    try:
        from web.app import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client
    finally:
        for p in patches:
            p.stop()
        get_settings.cache_clear()
