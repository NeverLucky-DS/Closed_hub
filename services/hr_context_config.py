from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)


@lru_cache
def _path() -> Path:
    return Path(__file__).resolve().parent.parent / "config" / "hr_sheet_context.json"


@lru_cache
def _data() -> dict:
    p = _path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        log.warning("hr_sheet_context.json not found, using empty HR context")
        return {"employers_hint": [], "venues_not_companies": [], "notes_ru": ""}
    except (json.JSONDecodeError, OSError) as e:
        log.warning("hr_sheet_context.json read failed: %s", e)
        return {"employers_hint": [], "venues_not_companies": [], "notes_ru": ""}


def prompt_blocks() -> tuple[str, str, str]:
    d = _data()
    emps = d.get("employers_hint") or []
    venues = d.get("venues_not_companies") or []
    note = str(d.get("notes_ru") or "").strip()
    employers_lines = "\n".join(f"- {x}" for x in emps) if emps else "(список пуст — ориентируйся на явные названия работодателя в тексте)"
    venues_lines = "\n".join(f"- {x}" for x in venues) if venues else "(нет)"
    return employers_lines, venues_lines, note
