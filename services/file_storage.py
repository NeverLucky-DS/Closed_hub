from __future__ import annotations

import logging
import shutil
from pathlib import Path

from config import get_settings

log = logging.getLogger(__name__)


def library_root() -> Path:
    return Path(get_settings().file_storage_path) / "library"


def profile_root() -> Path:
    return Path(get_settings().file_storage_path) / "profiles"


def events_covers_root() -> Path:
    return Path(get_settings().file_storage_path) / "events" / "covers"


def staging_dir_for_hash(sha256: str) -> Path:
    base = library_root() / "_staging" / sha256[:2]
    base.mkdir(parents=True, exist_ok=True)
    return base


def move_into_category_folder(current_path: str, category_slug: str, filename: str) -> str:
    root = library_root()
    dest_dir = root / category_slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    src = Path(current_path)
    dest = dest_dir / filename
    if src.resolve() == dest.resolve():
        return str(dest)
    if dest.exists():
        dest = dest_dir / f"{src.stem}_{src.suffix}"  # rare collision
    shutil.move(str(src), str(dest))
    log.info("file moved to library/%s/%s", category_slug, dest.name)
    return str(dest)
