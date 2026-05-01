from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from db import repo
from services.activity_points import points_for

if TYPE_CHECKING:
    from telegram import Bot

log = logging.getLogger(__name__)


async def award(
    pool,
    telegram_user_id: int,
    reason: str,
    meta: dict | None = None,
    *,
    bot: Bot | None = None,
    announcer_label: str | None = None,
) -> int:
    pts = points_for(reason)
    if pts <= 0:
        log.info(
            "metric type=activity_award reason=%s points=0 skipped=1 user=%s",
            reason,
            telegram_user_id,
        )
        return 0
    new_total = await repo.add_activity_points(pool, telegram_user_id, pts, reason, meta)
    log.info(
        "metric type=activity_award reason=%s points=%s user=%s total=%s meta=%s",
        reason,
        pts,
        telegram_user_id,
        new_total,
        json.dumps(meta or {}, ensure_ascii=False)[:200],
    )
    if bot is not None and announcer_label:
        from services import activity_announce

        try:
            await activity_announce.notify_award(
                bot,
                who_label=announcer_label,
                reason=reason,
                points=pts,
                total=new_total,
                meta=meta,
            )
        except Exception:
            log.warning("activity announce failed", exc_info=True)
    return pts
