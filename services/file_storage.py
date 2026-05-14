from __future__ import annotations

import logging
import shutil
from pathlib import Path

from config import get_settings
from utils.text_slug import slugify_folder

log = logging.getLogger(__name__)


def library_root() -> Path:
    return Path(get_settings().file_storage_path) / "library"


def profile_root() -> Path:
    return Path(get_settings().file_storage_path) / "profiles"


def company_root() -> Path:
    return Path(get_settings().file_storage_path) / "companies"


def events_covers_root() -> Path:
    return Path(get_settings().file_storage_path) / "events" / "covers"


def staging_dir_for_hash(sha256: str) -> Path:
    base = library_root() / "_staging" / sha256[:2]
    base.mkdir(parents=True, exist_ok=True)
    return base


def move_into_category_folder(current_path: str, category_slug: str, filename: str) -> str:
    """Перемещает файл в library/<slug>/, только внутри library_root (без .. и абсолютных путей)."""
    root = library_root().resolve()
    safe_slug = slugify_folder(category_slug)
    dest_dir = (root / safe_slug).resolve()
    if not dest_dir.is_relative_to(root):
        raise ValueError("unsafe category path")

    dest_dir.mkdir(parents=True, exist_ok=True)

    src = Path(current_path)
    if not src.is_absolute():
        src = Path.cwd() / src
    src = src.resolve()
    if not src.is_relative_to(root):
        raise ValueError("source file outside library root")

    base_name = Path(filename).name
    if not base_name or base_name != str(Path(filename)):
        raise ValueError("unsafe filename")

    dest = (dest_dir / base_name).resolve()
    if not dest.is_relative_to(dest_dir):
        raise ValueError("unsafe destination path")

    if src == dest:
        return str(dest)
    if dest.exists():
        dest = (dest_dir / f"{src.stem}_{src.suffix}").resolve()
        if not dest.is_relative_to(dest_dir):
            raise ValueError("unsafe collision path")
    shutil.move(str(src), str(dest))
    log.info("file moved to library/%s/%s", safe_slug, dest.name)
    return str(dest)
