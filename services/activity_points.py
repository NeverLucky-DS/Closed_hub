from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

_DEFAULT = {
    "hr_contact_confirmed": 15,
    "library_file_confirmed": 10,
    "event_published": 5,
    "interview_submitted": 12,
    "ml_forward_shared": 3,
}


@lru_cache
def _config_path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "activity_points.json"


def points_for(reason: str) -> int:
    path = _config_path()
    data = dict(_DEFAULT)
    try:
        raw = path.read_text(encoding="utf-8")
        data.update(json.loads(raw))
    except FileNotFoundError:
        log.debug("activity_points.json not found, using defaults")
    except (json.JSONDecodeError, OSError) as e:
        log.warning("activity_points.json read failed: %s", e)
    v = data.get(reason)
    if v is None:
        return 0
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0
