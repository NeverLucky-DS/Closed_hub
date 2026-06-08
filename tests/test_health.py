"""FastAPI health probes."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from starlette.requests import Request

from tests.mock_pool import mock_pool
from web.routers.health import health, ready


def _request_with_pool(pool: object | None) -> Request:
    app = SimpleNamespace(state=SimpleNamespace(pool=pool))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/ready",
        "headers": [],
        "app": app,
    }
    return Request(scope)


@pytest.mark.asyncio
async def test_health_returns_ok(app_client: AsyncClient) -> None:
    r = await app_client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_health_handler_unit() -> None:
    assert await health() == {"status": "ok"}


@pytest.mark.asyncio
async def test_ready_true_when_db_responds() -> None:
    request = _request_with_pool(mock_pool())
    result = await ready(request)
    assert result == {"ready": True}


@pytest.mark.asyncio
async def test_ready_false_without_pool() -> None:
    request = _request_with_pool(None)
    response = await ready(request)
    assert response.status_code == 503
