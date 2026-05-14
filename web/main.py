from __future__ import annotations

import logging
import os

import uvicorn

from config import get_settings

log = logging.getLogger(__name__)


def main() -> None:
    settings = get_settings()
    secret = settings.web_session_secret or os.environ.get("WEB_SESSION_SECRET")
    if not secret:
        raise SystemExit(
            "Задайте WEB_SESSION_SECRET (или web_session_secret в .env) для веб-сервера."
        )
    port = int(os.environ.get("WEB_PORT", "8000"))
    workers = int((os.environ.get("WEB_UVICORN_WORKERS") or "1").strip() or "1")
    # 0.0.0.0 — доступ с телефона в той же Wi‑Fi сети; 127.0.0.1 — только с этого Mac.
    host = (os.environ.get("WEB_HOST") or "0.0.0.0").strip() or "0.0.0.0"
    forwarded_allow_ips = (settings.web_forwarded_allow_ips or "127.0.0.1").strip()
    if workers > 1:
        log.warning(
            "WEB_UVICORN_WORKERS=%s: in-memory auth rate limits in web/app.py are "
            "per-process and inconsistent across workers; use 1 worker or move limits "
            "to Redis in the future.",
            workers,
        )
    uvicorn.run(
        "web.app:app",
        host=host,
        port=port,
        workers=workers,
        proxy_headers=settings.web_proxy_headers,
        forwarded_allow_ips=forwarded_allow_ips or "127.0.0.1",
    )


if __name__ == "__main__":
    main()
