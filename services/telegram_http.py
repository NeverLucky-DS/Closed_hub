from __future__ import annotations

import logging
from typing import Any

import httpx

from config import Settings, get_settings
from telegram.request import HTTPXRequest

log = logging.getLogger(__name__)


def telegram_proxy_chain(settings: Settings | None = None) -> list[str | None]:
    """Цепочка прокси для Bot API (HTTPS к api.telegram.org).

    Пустая настройка → один элемент ``None``: клиент берёт системные ``HTTP(S)_PROXY`` / ``ALL_PROXY``.
    Непустая строка → список URL через запятую (HTTP или SOCKS5), без отката на прямое соединение.
    """
    s = settings or get_settings()
    raw = (s.telegram_proxy_url or "").strip()
    if not raw:
        return [None]
    urls = [u.strip() for u in raw.split(",") if u.strip()]
    return urls if urls else [None]


def build_telegram_bot_httpx_request(settings: Settings | None = None) -> HTTPXRequest:
    """HTTP-клиент для python-telegram-bot (polling и все вызовы context.bot)."""
    s = settings or get_settings()
    chain = telegram_proxy_chain(s)
    first = chain[0]
    kw: dict[str, Any] = {
        "connect_timeout": 45.0,
        "read_timeout": 120.0,
        "write_timeout": 60.0,
        "media_write_timeout": 300.0,
        "pool_timeout": 15.0,
    }
    if first:
        kw["proxy_url"] = first
        if len(chain) > 1:
            log.info(
                "TELEGRAM_PROXY_URL: для бота используется только первый прокси; "
                "остальные из списка задействованы в веб-запросах к Bot API."
            )
    return HTTPXRequest(**kw)


async def telegram_api_post(url: str, **kwargs: Any) -> httpx.Response:
    """POST к api.telegram.org с перебором прокси из цепочки (как в telegram_proxy_chain)."""
    chain = telegram_proxy_chain()
    last_err: Exception | None = None
    for proxy in chain:
        client_kw: dict[str, Any] = {"timeout": 30.0}
        if proxy:
            client_kw["proxy"] = proxy
            client_kw["trust_env"] = False
        try:
            async with httpx.AsyncClient(**client_kw) as client:
                return await client.post(url, **kwargs)
        except (
            httpx.ProxyError,
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
        ) as e:
            last_err = e
            log.warning("Telegram Bot API: сбой запроса (прокси=%s): %s", proxy or "env", e)
            continue
    assert last_err is not None
    raise last_err
