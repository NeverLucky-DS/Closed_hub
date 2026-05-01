from __future__ import annotations

import os

import uvicorn

from config import get_settings


def main() -> None:
    settings = get_settings()
    secret = settings.web_session_secret or os.environ.get("WEB_SESSION_SECRET")
    if not secret:
        raise SystemExit(
            "Задайте WEB_SESSION_SECRET (или web_session_secret в .env) для веб-сервера."
        )
    port = int(os.environ.get("WEB_PORT", "8000"))
    uvicorn.run(
        "web.app:app",
        host="0.0.0.0",
        port=port,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
