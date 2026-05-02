from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import shutil
import time
import uuid
from urllib.parse import quote_plus
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import mimetypes
from datetime import date, datetime, timezone
from datetime import time as dt_time
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from config import get_settings
from db import repo
from db.pool import close_pool, create_pool
from db.schema_patch import apply_pending_patches
from services.file_storage import company_root, library_root, profile_root
from services import interviews_store
from utils.company_slug import slugify_company_name

log = logging.getLogger(__name__)

_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
_static_dir = Path(__file__).resolve().parent / "static"

_auth_rl: dict[str, float] = {}
_RL_WINDOW_SEC = 55.0

_REACTION_EMOJIS = ("👍", "❤️", "🔥", "🤔", "🎉")


def _rl_allow(key: str) -> bool:
    now = time.time()
    last = _auth_rl.get(key, 0.0)
    if now - last < _RL_WINDOW_SEC:
        return False
    _auth_rl[key] = now
    return True


def _otp_hash(secret: str, code: str) -> str:
    return hmac.new(secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def _github_ok(url: str) -> bool:
    u = (url or "").strip().rstrip("/")
    return u.startswith("https://github.com/") and len(u) > len("https://github.com/")


def _event_header(rec: Any) -> str:
    t = rec.get("normalized_title")
    if t and str(t).strip():
        return str(t).strip()
    raw = (rec.get("raw_text") or "").strip()
    for mark in ("[пересланное сообщение]", "[переслано]"):
        if raw.startswith(mark):
            raw = raw[len(mark) :].lstrip()
    line = raw.split("\n", 1)[0].strip()
    return (line[:120] + "…") if len(line) > 120 else line


def _event_summary(rec: Any) -> str:
    """AI-саммари если есть, иначе аккуратный фолбэк по raw_text."""
    s = rec.get("ai_summary")
    if s and str(s).strip():
        return str(s).strip()
    raw = (rec.get("raw_text") or "").strip()
    if not raw:
        return ""
    text = " ".join(raw.split())
    if len(text) <= 220:
        return text
    head = text[:220]
    cut = head.rfind(" ")
    return (head[:cut] if cut > 100 else head).rstrip(",.;:- ") + "…"


def _file_kind(mime: str | None, name: str | None) -> str:
    m = (mime or "").lower()
    n = (name or "").lower()
    if "pdf" in m or n.endswith(".pdf"):
        return "pdf"
    if m.startswith("image/") or n.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    return "other"


def _initial_for(name: str | None, fallback: str | int | None = None) -> str:
    s = (name or "").strip()
    if s:
        return s[0].upper()
    return str(fallback)[0].upper() if fallback else "?"


def _format_dt_short(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return ""


def _format_dt_rel(dt) -> str:
    if not dt:
        return ""
    try:
        now = datetime.now(timezone.utc)
        d = dt
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        sec = (now - d).total_seconds()
        if sec < 45:
            return "только что"
        if sec < 3600:
            m = int(sec // 60)
            return f"{m} мин назад"
        if sec < 86400:
            h = int(sec // 3600)
            return f"{h} ч назад"
        days = int(sec // 86400)
        if days == 1:
            return "вчера"
        return f"{days} дн. назад"
    except Exception:
        return ""


_NEWS_PLACEHOLDER_DIR = _static_dir / "news-placeholders"
_NEWS_PLACEHOLDER_NAMES = tuple(f"placeholder-{i}" for i in range(1, 6))


def _safe_event_cover_disk_path(rel: str | None) -> Path | None:
    if not rel or not str(rel).strip():
        return None
    part = str(rel).strip().replace("\\", "/")
    if ".." in part or part.startswith("/"):
        return None
    root = Path(get_settings().file_storage_path).resolve()
    full = (root / part).resolve()
    if not str(full).startswith(str(root)):
        return None
    if not full.is_file():
        return None
    return full


def _event_thumb_url(rec: Any) -> str:
    eid = int(rec["id"])
    cover = rec.get("cover_image_path")
    disk = _safe_event_cover_disk_path(cover)
    if disk is not None:
        return f"/api/events/{eid}/cover"
    idx = (eid % 5) + 1
    base = _NEWS_PLACEHOLDER_NAMES[idx - 1]
    for ext in (".webp", ".jpg", ".jpeg", ".png"):
        f = _NEWS_PLACEHOLDER_DIR / f"{base}{ext}"
        if f.is_file():
            return f"/static/news-placeholders/{base}{ext}"
    return ""


def _valid_photo_paths(raw: Any) -> list[str]:
    """Нормализует photo_paths из JSONB: только непустые пути вида uid/file.ext (без ..)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        if not s or s == "[]":
            return []
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for p in raw:
        part = str(p).strip().replace("\\", "/")
        if not part or part in seen or ".." in part or "/" not in part:
            continue
        seen.add(part)
        out.append(part)
    return out[:5]


def _company_thumb_url(rec: Any) -> str:
    cid = int(rec["id"])
    paths = _valid_photo_paths(rec.get("photo_paths"))
    if paths:
        fn = str(paths[0]).strip().replace("\\", "/").split("/")[-1]
        if fn:
            return f"/media/company/{cid}/{fn}"
    return ""


async def _pick_unique_company_slug(pool, name: str) -> str:
    base = slugify_company_name(name)
    for i in range(0, 500):
        slug = base if i == 0 else f"{base}-{i}"
        if not await repo.company_slug_taken(pool, slug):
            return slug
    return f"{base}-{secrets.token_hex(4)}"


async def _prepare_company_photo_blobs(
    photos: list[UploadFile],
) -> tuple[list[tuple[bytes, str]], str | None]:
    nonempty = [p for p in photos if p.filename]
    if not nonempty:
        return [], None
    if len(nonempty) > 5:
        return [], "Не больше пяти фотографий."
    settings = get_settings()
    max_b = settings.web_max_profile_photo_mb * 1024 * 1024
    allowed_mime = {"image/jpeg", "image/png", "image/webp"}
    out: list[tuple[bytes, str]] = []
    for up in nonempty:
        raw = await up.read()
        if len(raw) > max_b:
            return [], f"Файл слишком большой (макс. {settings.web_max_profile_photo_mb} МБ)"
        ct = (up.content_type or "").split(";")[0].strip().lower()
        if ct not in allowed_mime:
            return [], "Только JPEG, PNG или WebP для фото компании"
        if ct == "image/jpeg":
            ext = "jpg"
        elif ct == "image/png":
            ext = "png"
        else:
            ext = "webp"
        out.append((raw, ext))
    return out, None


def _write_company_photos(company_id: int, blobs: list[tuple[bytes, str]]) -> list[str]:
    if not blobs:
        return []
    dest_root = company_root() / str(company_id)
    dest_root.mkdir(parents=True, exist_ok=True)
    rel_paths: list[str] = []
    for raw, ext in blobs:
        fn = f"{uuid.uuid4().hex}.{ext}"
        (dest_root / fn).write_bytes(raw)
        rel_paths.append(f"{company_id}/{fn}")
    return rel_paths


def _cleanup_company_photos_disk(company_id: int, keep_paths: list[str]) -> None:
    root = company_root() / str(company_id)
    if not root.is_dir():
        return
    keep = {str(p).strip().replace("\\", "/") for p in keep_paths}
    for f in root.iterdir():
        if not f.is_file():
            continue
        rel = f"{company_id}/{f.name}"
        if rel not in keep:
            try:
                f.unlink()
            except OSError:
                pass


def _company_cover_class(rec: Any) -> str:
    key = str(rec.get("name") or rec.get("slug") or "x")
    n = hashlib.md5(key.encode()).digest()[0] % 6 + 1
    return f"cover-{n}"


def _ru_plural(n: int, forms: tuple[str, str, str]) -> str:
    n_abs = abs(int(n))
    mod10 = n_abs % 10
    mod100 = n_abs % 100
    if mod10 == 1 and mod100 != 11:
        return forms[0]
    if 2 <= mod10 <= 4 and not (12 <= mod100 <= 14):
        return forms[1]
    return forms[2]


def _event_badges(rec: Any) -> list[dict]:
    """Бейджи для карточки события: 'Скоро', 'Новое', 'До <дата>'."""
    from datetime import datetime, timedelta, timezone

    out: list[dict] = []
    now = datetime.now(timezone.utc)
    ends_at = rec.get("ends_at")
    created_at = rec.get("created_at")
    if ends_at and ends_at > now and (ends_at - now) <= timedelta(days=7):
        out.append({"text": "Скоро дедлайн", "kind": "warn"})
    if created_at and (now - created_at) <= timedelta(hours=48):
        out.append({"text": "Новое", "kind": "accent"})
    if ends_at:
        try:
            out.append({"text": f"до {ends_at.strftime('%d.%m')}", "kind": "muted"})
        except Exception:
            pass
    return out


def _events_metrics(events: list[Any]) -> dict:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    soon = sum(
        1
        for e in events
        if e.get("ends_at") and e["ends_at"] > now and (e["ends_at"] - now) <= timedelta(days=7)
    )
    return {"total": len(events), "soon": soon}


_templates.env.globals["icons_path"] = "_icons.html"
_templates.env.filters["event_header"] = _event_header
_templates.env.filters["event_summary"] = _event_summary
_templates.env.filters["file_kind"] = lambda r: _file_kind(r.get("mime_type"), r.get("original_filename"))
_templates.env.filters["initial"] = _initial_for
_templates.env.filters["dt_short"] = _format_dt_short
_templates.env.filters["dt_rel"] = _format_dt_rel
_templates.env.filters["event_thumb"] = _event_thumb_url
_templates.env.filters["ru_plural"] = _ru_plural
_templates.env.filters["event_badges"] = _event_badges
_templates.env.filters["valid_photos"] = _valid_photo_paths
_templates.env.filters["company_thumb"] = lambda r: _company_thumb_url(r)
_templates.env.filters["company_cover_class"] = _company_cover_class
_templates.env.globals["hub_settings"] = get_settings()


class EventEndsAtBody(BaseModel):
    ends_at: str = Field(..., min_length=10, max_length=32)


class EventReactBody(BaseModel):
    emoji: str = ""


def _no_store_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, max-age=0, must-revalidate",
        "Pragma": "no-cache",
        "Expires": "0",
    }


async def _send_telegram_code(chat_id: int, code: str) -> None:
    settings = get_settings()
    token = settings.telegram_bot_token
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    text = f"Код для входа на сайт хаба: <b>{code}</b>\n\nЕсли это не вы — проигнорируйте."
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(
            url,
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not r.is_success or not data.get("ok", False):
            desc = data.get("description", r.text[:200])
            log.warning("telegram sendMessage failed: %s", desc)
            raise HTTPException(
                status_code=502,
                detail="Не удалось отправить код в Telegram. Откройте бота и нажмите /start.",
            )


async def _telegram_try_delete_message(chat_id: int | str, message_id: int) -> bool:
    settings = get_settings()
    token = settings.telegram_bot_token
    url = f"https://api.telegram.org/bot{token}/deleteMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.post(url, json={"chat_id": chat_id, "message_id": message_id})
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.is_success and data.get("ok", False):
            return True
        log.warning("telegram deleteMessage failed: %s", data.get("description", r.text[:200]))
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await create_pool()
    await apply_pending_patches(pool)
    app.state.pool = pool
    profile_root().mkdir(parents=True, exist_ok=True)
    company_root().mkdir(parents=True, exist_ok=True)
    yield
    await close_pool(pool)


def _session_secret() -> str:
    s = get_settings().web_session_secret
    if not s:
        raise RuntimeError("web_session_secret / WEB_SESSION_SECRET не задан")
    return s


app = FastAPI(title="Closed hub web", lifespan=lifespan)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_secret(),
    max_age=14 * 24 * 3600,
    same_site="lax",
    https_only=False,
)

if _static_dir.is_dir():
    app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


def pool_dep(request: Request):
    return request.app.state.pool


def session_uid(request: Request) -> int | None:
    raw = request.session.get("uid")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def require_uid_api(request: Request, pool=Depends(pool_dep)) -> int:
    uid = session_uid(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Нужна авторизация")
    st = await repo.member_status(pool, uid)
    if st != "active":
        request.session.clear()
        raise HTTPException(status_code=403, detail="Нет доступа")
    return uid


async def require_web_admin(uid: int = Depends(require_uid_api)) -> int:
    if not is_web_admin(uid):
        raise HTTPException(status_code=403, detail="Нужны права администратора")
    return uid


@app.get("/", response_class=HTMLResponse)
async def root(request: Request, pool=Depends(pool_dep)):
    if session_uid(request) is None:
        return RedirectResponse("/login", status_code=302)
    uid = session_uid(request)
    assert uid is not None
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    return RedirectResponse("/library", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if session_uid(request) is not None:
        return RedirectResponse("/library", status_code=302)
    return _templates.TemplateResponse(
        request,
        "login.html",
        {"title": "Вход", "step": 1},
    )


@app.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)


@app.post("/api/auth/request")
async def auth_request(
    request: Request,
    pool=Depends(pool_dep),
):
    settings = get_settings()
    secret = settings.web_session_secret
    assert secret
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Неверный JSON")
    uid_raw = body.get("telegram_user_id")
    try:
        uid = int(uid_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Нужен числовой Telegram ID")

    client_ip = request.client.host if request.client else ""
    rl_key = f"{client_ip}:{uid}"
    if not _rl_allow(rl_key):
        raise HTTPException(status_code=429, detail="Слишком часто. Подождите минуту.")

    if await repo.member_status(pool, uid) != "active":
        raise HTTPException(status_code=403, detail="Пользователь не найден или нет доступа")

    code = f"{secrets.randbelow(900000) + 100000:06d}"
    digest = _otp_hash(secret, code)
    from datetime import datetime, timedelta, timezone

    exp = datetime.now(timezone.utc) + timedelta(seconds=settings.web_auth_code_ttl_sec)
    await repo.insert_web_login_code(pool, uid, digest, exp)
    await _send_telegram_code(uid, code)
    return JSONResponse({"ok": True})


@app.post("/api/auth/verify")
async def auth_verify(request: Request, pool=Depends(pool_dep)):
    settings = get_settings()
    secret = settings.web_session_secret
    assert secret
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Неверный JSON")
    uid_raw = body.get("telegram_user_id")
    code = str(body.get("code", "")).strip()
    try:
        uid = int(uid_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Нужен числовой Telegram ID")
    if len(code) != 6 or not code.isdigit():
        raise HTTPException(status_code=400, detail="Неверный код")

    if await repo.member_status(pool, uid) != "active":
        raise HTTPException(status_code=403, detail="Нет доступа")

    row = await repo.fetch_valid_web_login_code(pool, uid)
    if not row:
        raise HTTPException(status_code=400, detail="Код просрочен или уже использован")

    expect = row["code_hash"]
    if not hmac.compare_digest(expect, _otp_hash(secret, code)):
        raise HTTPException(status_code=400, detail="Неверный код")

    await repo.consume_web_login_code(pool, int(row["id"]))
    await repo.ensure_member_profile_row(pool, uid)
    request.session["uid"] = uid
    return JSONResponse({"ok": True})


def is_web_admin(uid: int) -> bool:
    return uid in get_settings().web_admin_id_set


def _normalize_reaction_emoji(raw: str) -> str | None:
    s = (raw or "").strip()
    if not s:
        return None
    if s in _REACTION_EMOJIS:
        return s
    return None


def _re_totals_map(eids: list[int], count_rows: list[Any]) -> dict[int, dict[str, int]]:
    m: dict[int, dict[str, int]] = {eid: {} for eid in eids}
    for r in count_rows:
        eid = int(r["event_id"])
        if eid in m:
            m[eid][str(r["emoji"])] = int(r["n"])
    return m


async def _build_feed_social(pool, viewer_uid: int, events: list[Any]) -> dict[str, Any]:
    eids = [int(e["id"]) for e in events]
    if not eids:
        return {"re_mine": {}, "re_totals": {}}
    count_rows = await repo.event_reaction_counts(pool, eids)
    re_mine_raw = await repo.event_user_reactions_map(pool, viewer_uid, eids)
    re_mine: dict[int, str | None] = {eid: re_mine_raw.get(eid) for eid in eids}
    re_totals = _re_totals_map(eids, count_rows)
    return {"re_mine": re_mine, "re_totals": re_totals}


def _safe_under(root: Path, rel: str) -> Path | None:
    try:
        candidate = (root / rel).resolve()
        root_r = root.resolve()
        if not str(candidate).startswith(str(root_r)):
            return None
        return candidate
    except Exception:
        return None


def _unlink_library_file_if_safe(storage_path: str) -> None:
    path = Path(storage_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    lib = library_root().resolve()
    if not str(path).startswith(str(lib)):
        return
    if path.is_file():
        path.unlink()


def _library_file_disk_path(row: Any) -> Path:
    path = Path(row["storage_path"])
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    lib = library_root().resolve()
    if not str(path).startswith(str(lib)):
        raise HTTPException(status_code=404)
    if not path.is_file():
        raise HTTPException(status_code=404)
    return path


@app.get("/api/library/{file_id}/raw")
async def library_file_raw(
    file_id: int,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
    dl: int = Query(0, description="1 = скачать файлом, 0 = показать inline (превью)"),
):
    row = await repo.get_file_record(pool, file_id)
    if not row or row["status"] != "confirmed":
        raise HTTPException(status_code=404)
    path = _library_file_disk_path(row)
    mime = row.get("mime_type") or "application/octet-stream"
    name = row.get("original_filename") or path.name
    if dl == 1:
        return FileResponse(
            path,
            media_type=mime,
            filename=name,
            content_disposition_type="attachment",
        )
    return FileResponse(
        path,
        media_type=mime,
        filename=None,
        content_disposition_type="inline",
    )


@app.get("/media/profile/{telegram_user_id}/{filename}")
async def profile_media(
    telegram_user_id: int,
    filename: str,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404)
    prof = await repo.get_member_profile(pool, telegram_user_id)
    if not prof:
        raise HTTPException(status_code=404)
    paths = _valid_photo_paths(prof.get("photo_paths"))
    rel = f"{telegram_user_id}/{filename}"
    ok = any(str(p).replace("\\", "/") == rel for p in paths)
    if not ok:
        raise HTTPException(status_code=404)
    root = profile_root().resolve()
    full = _safe_under(root, rel)
    if not full or not full.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(full)


@app.get("/media/company/{company_id}/{filename}")
async def company_media(
    company_id: int,
    filename: str,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
):
    if ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=404)
    row = await repo.get_company_by_id(pool, company_id)
    if not row:
        raise HTTPException(status_code=404)
    paths = _valid_photo_paths(row.get("photo_paths"))
    rel = f"{company_id}/{filename}"
    ok = any(str(p).replace("\\", "/") == rel for p in paths)
    if not ok:
        raise HTTPException(status_code=404)
    root = company_root().resolve()
    full = _safe_under(root, rel)
    if not full or not full.is_file():
        raise HTTPException(status_code=404)
    mime, _ = mimetypes.guess_type(str(full))
    return FileResponse(full, media_type=mime or "image/jpeg")


@app.get("/api/profile/{telegram_user_id}/resume")
async def profile_resume_download(
    telegram_user_id: int,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
):
    prof = await repo.get_member_profile(pool, telegram_user_id)
    if not prof or not prof.get("resume_path"):
        raise HTTPException(status_code=404)
    rel = str(prof["resume_path"]).strip().replace("\\", "/")
    if ".." in rel or not rel.startswith(f"{telegram_user_id}/"):
        raise HTTPException(status_code=404)
    root = profile_root().resolve()
    full = _safe_under(root, rel)
    if not full or not full.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(
        full,
        media_type="application/pdf",
        filename="resume.pdf",
        content_disposition_type="attachment",
    )


@app.get("/api/events/{event_id}/cover")
async def event_cover_image(
    event_id: int,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
):
    row = await repo.fetch_published_event(pool, event_id)
    if not row or not row.get("cover_image_path"):
        raise HTTPException(status_code=404)
    full = _safe_event_cover_disk_path(str(row["cover_image_path"]))
    if full is None:
        raise HTTPException(status_code=404)
    mime, _ = mimetypes.guess_type(str(full))
    return FileResponse(full, media_type=mime or "image/jpeg")


@app.patch("/api/events/{event_id}/ends_at")
async def api_patch_event_ends_at(
    event_id: int,
    body: EventEndsAtBody,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
):
    row = await repo.fetch_published_event(pool, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    raw = (body.ends_at or "").strip()[:10]
    try:
        d = date.fromisoformat(raw)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ожидается дата YYYY-MM-DD")
    ends = datetime.combine(d, dt_time(23, 59, 59), tzinfo=timezone.utc)
    ok = await repo.update_event_ends_at(pool, event_id, ends)
    if not ok:
        raise HTTPException(status_code=404, detail="Не обновлено")
    return JSONResponse({"ok": True, "ends_at": ends.isoformat()})


@app.delete("/api/library/{file_id}")
async def api_delete_library_file(
    file_id: int,
    pool=Depends(pool_dep),
    _admin: int = Depends(require_web_admin),
):
    row = await repo.mark_library_file_deleted(pool, file_id)
    if not row:
        raise HTTPException(status_code=404, detail="Файл не найден или уже удалён")
    _unlink_library_file_if_safe(str(row["storage_path"]))
    return JSONResponse({"ok": True})


@app.delete("/api/events/{event_id}")
async def api_hide_event(
    event_id: int,
    pool=Depends(pool_dep),
    _admin: int = Depends(require_web_admin),
):
    ev = await repo.fetch_event_for_admin(pool, event_id)
    if not ev or str(ev["status"]) != "published":
        raise HTTPException(status_code=404, detail="Событие не найдено или уже скрыто")
    ok = await repo.hide_published_event(pool, event_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Не удалось скрыть")
    mid = ev.get("published_message_id")
    chat = get_settings().telegram_group_chat_id
    if mid and chat is not None:
        await _telegram_try_delete_message(chat, int(mid))
    return JSONResponse({"ok": True})


@app.post("/api/events/{event_id}/react")
async def api_event_react(
    event_id: int,
    body: EventReactBody,
    pool=Depends(pool_dep),
    uid: int = Depends(require_uid_api),
):
    row = await repo.fetch_published_event(pool, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="Событие не найдено")
    em = _normalize_reaction_emoji(body.emoji)
    if body.emoji.strip() and em is None:
        raise HTTPException(status_code=400, detail="Недопустимая реакция")
    if em is None:
        await repo.delete_event_reaction(pool, event_id, uid)
    else:
        await repo.upsert_event_reaction(pool, event_id, uid, em)
    count_rows = await repo.event_reaction_counts(pool, [event_id])
    counts = [
        {"emoji": str(r["emoji"]), "n": int(r["n"])}
        for r in count_rows
        if int(r["event_id"]) == event_id
    ]
    mine_map = await repo.event_user_reactions_map(pool, uid, [event_id])
    return JSONResponse({"ok": True, "counts": counts, "mine": mine_map.get(event_id)})


@app.get("/library", response_class=HTMLResponse)
async def page_library(
    request: Request,
    cat: str | None = None,
    file: int | None = None,
    pool=Depends(pool_dep),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    cats = await repo.list_categories_with_counts(pool)

    if not cat:
        return _templates.TemplateResponse(
            request,
            "library.html",
            {
                "title": "Файлы",
                "nav": "library",
                "uid": uid,
                "view": "folders",
                "categories": cats,
                "is_admin": is_web_admin(uid),
            },
            headers=_no_store_headers(),
        )

    cat_label = next((c["label_ru"] for c in cats if c["slug"] == cat), cat)
    files = await repo.list_library_files(pool, limit=500, category_slug=cat)
    selected = None
    if file:
        selected = next((f for f in files if int(f["id"]) == int(file)), None)
    if selected is None and files:
        selected = files[0]
    companies_attach = await repo.list_companies_compact(pool, 100)
    attach_err = request.query_params.get("attach_err")
    return _templates.TemplateResponse(
        request,
        "library.html",
        {
            "title": cat_label,
            "nav": "library",
            "uid": uid,
            "view": "files",
            "category": cat,
            "category_label": cat_label,
            "categories": cats,
            "files": files,
            "selected": selected,
            "is_admin": is_web_admin(uid),
            "companies_for_attach": companies_attach,
            "attach_err": attach_err,
        },
        headers=_no_store_headers(),
    )


@app.post("/library/attach-company")
async def library_attach_company(
    request: Request,
    pool=Depends(pool_dep),
    cat: str = Form(""),
    file_id: str = Form(""),
    company_id: str = Form(""),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    try:
        fid = int((file_id or "").strip())
        cid = int((company_id or "").strip())
    except ValueError:
        q = f"cat={cat}&file={file_id}&attach_err=bad_params" if cat else "attach_err=bad_params"
        return RedirectResponse(f"/library?{q}", status_code=303)
    frow = await repo.get_file_record(pool, fid)
    if not frow or int(frow["uploaded_by"]) != uid:
        return RedirectResponse(
            f"/library?cat={cat}&file={fid}&attach_err=forbidden",
            status_code=303,
        )
    res = await repo.link_company_file(pool, cid, fid, uid, None)
    err_map = {
        "bad_file": "not_file",
        "duplicate": "dup",
        "missing_company": "no_company",
        "ok": "",
    }
    suf = err_map.get(res, "fail")
    redir = f"/library?cat={cat}&file={fid}" if cat else "/library"
    if suf:
        redir += f"&attach_err={suf}"
    return RedirectResponse(redir, status_code=303)


@app.get("/feed", response_class=HTMLResponse)
async def page_feed(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    events = await repo.list_events_feed(pool, limit=80)
    metrics = _events_metrics(events)
    social = await _build_feed_social(pool, uid, events)
    return _templates.TemplateResponse(
        request,
        "feed.html",
        {
            "title": "Новости",
            "nav": "feed",
            "events": events,
            "metrics": metrics,
            "uid": uid,
            "is_admin": is_web_admin(uid),
            "re_mine": social["re_mine"],
            "re_totals": social["re_totals"],
            "reaction_emojis": _REACTION_EMOJIS,
        },
        headers=_no_store_headers(),
    )


def _optional_date_start(s: str | None) -> datetime | None:
    raw = (s or "").strip()
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw[:10])
        return datetime.combine(d, dt_time(0, 0, 0), tzinfo=timezone.utc)
    except ValueError:
        return None


def _optional_date_end(s: str | None) -> datetime | None:
    raw = (s or "").strip()
    if not raw:
        return None
    try:
        d = date.fromisoformat(raw[:10])
        return datetime.combine(d, dt_time(23, 59, 59), tzinfo=timezone.utc)
    except ValueError:
        return None


@app.get("/hackathons", response_class=HTMLResponse)
async def page_hackathons(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    teams = await repo.list_hackathon_teams(pool, limit=100)
    return _templates.TemplateResponse(
        request,
        "hackathons.html",
        {
            "title": "Хакатоны и мероприятия",
            "nav": "hackathons",
            "uid": uid,
            "teams": teams,
        },
        headers=_no_store_headers(),
    )


@app.get("/hackathons/create", response_class=HTMLResponse)
async def page_hackathon_create_get(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    return _templates.TemplateResponse(
        request,
        "hackathon_create.html",
        {"title": "Новая команда", "nav": "hackathons", "uid": uid, "error": None},
        headers=_no_store_headers(),
    )


@app.post("/hackathons/create")
async def page_hackathon_create_post(
    request: Request,
    pool=Depends(pool_dep),
    title: str = Form(""),
    description: str = Form(""),
    max_members: int = Form(4),
    starts_at: str = Form(""),
    ends_at: str = Form(""),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    t = title.strip()
    desc = (description or "").strip()
    if len(t) < 2 or len(t) > 200:
        return _templates.TemplateResponse(
            request,
            "hackathon_create.html",
            {
                "title": "Новая команда",
                "nav": "hackathons",
                "uid": uid,
                "error": "Название: от 2 до 200 символов.",
            },
            status_code=400,
        )
    if len(desc) > 8000:
        return _templates.TemplateResponse(
            request,
            "hackathon_create.html",
            {
                "title": "Новая команда",
                "nav": "hackathons",
                "uid": uid,
                "error": "Описание слишком длинное (макс. 8000 символов).",
            },
            status_code=400,
        )
    try:
        mm = int(max_members)
    except (TypeError, ValueError):
        mm = 0
    if mm < 2 or mm > 30:
        return _templates.TemplateResponse(
            request,
            "hackathon_create.html",
            {
                "title": "Новая команда",
                "nav": "hackathons",
                "uid": uid,
                "error": "Размер команды: от 2 до 30 человек (включая тебя).",
            },
            status_code=400,
        )
    s_dt = _optional_date_start(starts_at)
    e_dt = _optional_date_end(ends_at)
    if s_dt and e_dt and e_dt < s_dt:
        return _templates.TemplateResponse(
            request,
            "hackathon_create.html",
            {
                "title": "Новая команда",
                "nav": "hackathons",
                "uid": uid,
                "error": "Дата окончания раньше даты начала.",
            },
            status_code=400,
        )

    tid = await repo.create_hackathon_team(
        pool,
        title=t,
        description=desc,
        starts_at=s_dt,
        ends_at=e_dt,
        max_members=mm,
        creator_telegram_id=uid,
    )
    return RedirectResponse(f"/hackathons/{tid}", status_code=303)


@app.get("/hackathons/{team_id}", response_class=HTMLResponse)
async def page_hackathon_detail(team_id: int, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    team = await repo.get_hackathon_team(pool, team_id)
    if not team:
        raise HTTPException(status_code=404, detail="Команда не найдена")
    members = await repo.list_hackathon_team_members(pool, team_id)
    pending: list[Any] = []
    if int(team["creator_telegram_id"]) == uid:
        pending = await repo.list_hackathon_pending_applications(pool, team_id)
    app_row = await repo.get_hackathon_application(pool, team_id, uid)
    is_member = await repo.is_hackathon_team_member(pool, team_id, uid)
    toast = request.query_params.get("toast")
    return _templates.TemplateResponse(
        request,
        "hackathon_detail.html",
        {
            "title": team["title"],
            "nav": "hackathons",
            "uid": uid,
            "team": team,
            "members": members,
            "pending": pending,
            "app_row": app_row,
            "is_member": is_member,
            "toast": toast,
        },
        headers=_no_store_headers(),
    )


@app.post("/hackathons/{team_id}/apply")
async def hackathon_apply_post(team_id: int, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    code = await repo.apply_hackathon_team(pool, team_id, uid)
    return RedirectResponse(f"/hackathons/{team_id}?toast={code}", status_code=303)


@app.post("/hackathons/{team_id}/applications/{application_id}/accept")
async def hackathon_accept_post(
    team_id: int,
    application_id: int,
    request: Request,
    pool=Depends(pool_dep),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    r = await repo.hackathon_accept_application(pool, application_id, uid)
    suffix = "accepted_ok" if r == "ok" else r
    return RedirectResponse(f"/hackathons/{team_id}?toast={suffix}", status_code=303)


@app.post("/hackathons/{team_id}/applications/{application_id}/reject")
async def hackathon_reject_post(
    team_id: int,
    application_id: int,
    request: Request,
    pool=Depends(pool_dep),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    r = await repo.hackathon_reject_application(pool, application_id, uid)
    suffix = "rejected_ok" if r == "ok" else r
    return RedirectResponse(f"/hackathons/{team_id}?toast={suffix}", status_code=303)


@app.get("/today", response_class=HTMLResponse)
async def page_today(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    strip = await repo.list_events_digest(pool, limit=35)
    metrics = _events_metrics(strip)
    return _templates.TemplateResponse(
        request,
        "today.html",
        {
            "title": "Выжимка",
            "nav": "today",
            "events": strip,
            "metrics": metrics,
            "uid": uid,
        },
        headers=_no_store_headers(),
    )


@app.get("/companies", response_class=HTMLResponse)
async def page_companies(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    rows = await repo.list_companies_with_counts(pool)
    return _templates.TemplateResponse(
        request,
        "companies.html",
        {
            "title": "Компании",
            "nav": "companies",
            "uid": uid,
            "companies": rows,
        },
        headers=_no_store_headers(),
    )


@app.get("/companies/new", response_class=HTMLResponse)
async def page_company_new_get(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    return _templates.TemplateResponse(
        request,
        "company_new.html",
        {"title": "Новая компания", "nav": "companies", "uid": uid, "error": None},
        headers=_no_store_headers(),
    )


@app.post("/companies/new")
async def page_company_new_post(
    request: Request,
    pool=Depends(pool_dep),
    name: str = Form(""),
    description: str = Form(""),
    photo: UploadFile | None = File(None),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    n = name.strip()
    if len(n) < 2 or len(n) > 200:
        return _templates.TemplateResponse(
            request,
            "company_new.html",
            {
                "title": "Новая компания",
                "nav": "companies",
                "uid": uid,
                "error": "Название: от 2 до 200 символов.",
            },
            status_code=400,
            headers=_no_store_headers(),
        )
    desc = (description or "").strip()
    if len(desc) > 8000:
        return _templates.TemplateResponse(
            request,
            "company_new.html",
            {
                "title": "Новая компания",
                "nav": "companies",
                "uid": uid,
                "error": "Описание слишком длинное.",
            },
            status_code=400,
            headers=_no_store_headers(),
        )

    photos_in = [photo] if (photo and photo.filename) else []
    blobs, err = await _prepare_company_photo_blobs(photos_in)
    if err:
        return _templates.TemplateResponse(
            request,
            "company_new.html",
            {
                "title": "Новая компания",
                "nav": "companies",
                "uid": uid,
                "error": err,
            },
            status_code=400,
            headers=_no_store_headers(),
        )
    slug = await _pick_unique_company_slug(pool, n)
    cid = await repo.insert_company(pool, slug, n, desc or None, uid, [])
    rel_paths = _write_company_photos(cid, blobs)
    if rel_paths:
        await repo.update_company_photo_paths(pool, cid, rel_paths)
    return RedirectResponse(f"/companies/{slug}", status_code=303)


@app.get("/companies/{slug}", response_class=HTMLResponse)
async def page_company_hub(slug: str, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    tabs = await repo.get_company_tab_counts(pool, int(company["id"]))
    err = request.query_params.get("err")
    adm = is_web_admin(uid)
    show_delete = int(company["created_by"]) == uid or adm
    can_manage_company = show_delete
    return _templates.TemplateResponse(
        request,
        "company_hub.html",
        {
            "title": company["name"],
            "nav": "companies",
            "uid": uid,
            "company": company,
            "tabs": tabs,
            "error": err,
            "show_delete": show_delete,
            "is_admin": adm,
            "can_manage_company": can_manage_company,
        },
        headers=_no_store_headers(),
    )


@app.post("/companies/{slug}/photo")
async def company_photo_post(
    slug: str,
    request: Request,
    pool=Depends(pool_dep),
    photo: UploadFile | None = File(None),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    if int(company["created_by"]) != uid and not is_web_admin(uid):
        raise HTTPException(status_code=403, detail="Только автор карточки или админ")
    if not photo or not photo.filename:
        return RedirectResponse(
            f"/companies/{slug}?err={quote_plus('Выбери файл изображения')}",
            status_code=303,
        )
    blobs, err = await _prepare_company_photo_blobs([photo])
    if err:
        return RedirectResponse(
            f"/companies/{slug}?err={quote_plus(err)}",
            status_code=303,
        )
    cid = int(company["id"])
    new_paths = _write_company_photos(cid, blobs)
    _cleanup_company_photos_disk(cid, new_paths)
    await repo.update_company_photo_paths(pool, cid, new_paths)
    return RedirectResponse(f"/companies/{slug}", status_code=303)


@app.post("/companies/{slug}/photo-clear")
async def company_photo_clear_post(slug: str, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    if int(company["created_by"]) != uid and not is_web_admin(uid):
        raise HTTPException(status_code=403)
    cid = int(company["id"])
    _cleanup_company_photos_disk(cid, [])
    await repo.update_company_photo_paths(pool, cid, [])
    return RedirectResponse(f"/companies/{slug}", status_code=303)


@app.post("/companies/{slug}/delete")
async def company_delete_post(slug: str, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    cid = int(company["id"])
    if int(company["created_by"]) != uid and not is_web_admin(uid):
        raise HTTPException(status_code=403, detail="Удалить может автор карточки или админ")
    ok = await repo.delete_company(pool, cid)
    if ok:
        media = company_root() / str(cid)
        if media.is_dir():
            shutil.rmtree(media, ignore_errors=True)
    return RedirectResponse("/companies", status_code=303)


@app.get("/companies/{slug}/hr", response_class=HTMLResponse)
async def page_company_hr(slug: str, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    cid = int(company["id"])
    err = request.query_params.get("err")
    return _templates.TemplateResponse(
        request,
        "company_hr.html",
        {
            "title": f"{company['name']} — HR",
            "nav": "companies",
            "uid": uid,
            "company": company,
            "hr_list": await repo.list_hr_for_company(pool, cid),
            "hr_picker": await repo.list_hr_contacts_for_company_picker(pool, cid),
            "error": err,
        },
        headers=_no_store_headers(),
    )


@app.get("/companies/{slug}/interviews", response_class=HTMLResponse)
async def page_company_interviews(slug: str, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    cid = int(company["id"])
    err = request.query_params.get("err")
    return _templates.TemplateResponse(
        request,
        "company_interviews.html",
        {
            "title": f"{company['name']} — Собесы",
            "nav": "companies",
            "uid": uid,
            "company": company,
            "reviews": await repo.list_company_interview_reviews(pool, cid),
            "hr_picker": await repo.list_hr_contacts_for_company_picker(pool, cid),
            "error": err,
        },
        headers=_no_store_headers(),
    )


@app.get("/companies/{slug}/files", response_class=HTMLResponse)
async def page_company_files(slug: str, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404, detail="Компания не найдена")
    cid = int(company["id"])
    err = request.query_params.get("err")
    return _templates.TemplateResponse(
        request,
        "company_files.html",
        {
            "title": f"{company['name']} — Файлы",
            "nav": "companies",
            "uid": uid,
            "company": company,
            "file_links": await repo.list_company_files_with_meta(pool, cid),
            "recent_files": await repo.list_recent_confirmed_files_for_uploader(pool, uid, 60),
            "error": err,
        },
        headers=_no_store_headers(),
    )


@app.post("/companies/{slug}/review")
async def company_add_review(
    slug: str,
    request: Request,
    pool=Depends(pool_dep),
    body: str = Form(""),
    hr_contact_id: str = Form(""),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    hid: int | None = None
    raw_hr = (hr_contact_id or "").strip()
    if raw_hr:
        try:
            hid = int(raw_hr)
        except ValueError:
            return RedirectResponse(
                f"/companies/{slug}/interviews?err=Некорректный+HR", status_code=303
            )
    code = await repo.insert_company_interview_review(
        pool, int(company["id"]), uid, body, hid
    )
    if code == "short_body":
        return RedirectResponse(
            f"/companies/{slug}/interviews?err=Минимум+10+символов+в+отзыве+или+выбери+HR",
            status_code=303,
        )
    if code == "bad_hr":
        return RedirectResponse(
            f"/companies/{slug}/interviews?err=Контакт+HR+не+найдён", status_code=303
        )
    if code == "hr_other_company":
        return RedirectResponse(
            f"/companies/{slug}/interviews?err=Этот+HR+уже+привязан+к+другой+компании",
            status_code=303,
        )
    review_text = (body or "").strip()
    hr_ref: str | None = None
    if hid is not None:
        hrow = await repo.get_hr_contact(pool, hid)
        if hrow:
            hr_ref = str(hrow.get("contact_ref") or "").strip() or None
    body_for_file = review_text if review_text else "—"
    try:
        interviews_store.append_site_review_for_company(
            company_display_name=str(company["name"]),
            author_telegram_id=int(uid),
            body=body_for_file,
            hr_contact_ref=hr_ref,
        )
    except Exception:
        log.exception(
            "append_site_review_for_company failed company_id=%s slug=%s",
            company["id"],
            slug,
        )
    return RedirectResponse(f"/companies/{slug}/interviews", status_code=303)


@app.post("/companies/{slug}/link-file")
async def company_link_file(
    slug: str,
    request: Request,
    pool=Depends(pool_dep),
    file_id_select: str = Form(""),
    file_id_manual: str = Form(""),
    note: str = Form(""),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    raw_id = (file_id_select or "").strip() or (file_id_manual or "").strip()
    try:
        fid = int(raw_id)
    except ValueError:
        return RedirectResponse(
            f"/companies/{slug}/files?err=Выбери+файл+из+списка+или+укажи+id", status_code=303
        )
    res = await repo.link_company_file(
        pool, int(company["id"]), fid, uid, (note or "").strip() or None
    )
    if res == "bad_file":
        return RedirectResponse(
            f"/companies/{slug}/files?err=Файл+не+найден+или+ещё+не+в+библиотеке",
            status_code=303,
        )
    if res == "duplicate":
        return RedirectResponse(
            f"/companies/{slug}/files?err=Этот+файл+уже+прикреплён", status_code=303
        )
    return RedirectResponse(f"/companies/{slug}/files", status_code=303)


@app.post("/companies/{slug}/link-hr")
async def company_link_hr(
    slug: str,
    request: Request,
    pool=Depends(pool_dep),
    hr_contact_id: str = Form(""),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    try:
        hid = int((hr_contact_id or "").strip())
    except ValueError:
        return RedirectResponse(f"/companies/{slug}/hr?err=Выбери+контакт+HR", status_code=303)
    ok = await repo.set_hr_contact_company(pool, hid, int(company["id"]))
    if not ok:
        return RedirectResponse(
            f"/companies/{slug}/hr?err=Не+удалось+привязать+—+контакт+уже+у+другой+компании+или+не+подтверждён",
            status_code=303,
        )
    return RedirectResponse(f"/companies/{slug}/hr", status_code=303)


@app.post("/companies/{slug}/unlink-hr")
async def company_unlink_hr(
    slug: str,
    request: Request,
    pool=Depends(pool_dep),
    hr_contact_id: str = Form(""),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    company = await repo.get_company_by_slug(pool, slug)
    if not company:
        raise HTTPException(status_code=404)
    try:
        hid = int((hr_contact_id or "").strip())
    except ValueError:
        return RedirectResponse(f"/companies/{slug}/hr", status_code=303)
    await repo.unlink_hr_contact_from_company(pool, hid, int(company["id"]))
    return RedirectResponse(f"/companies/{slug}/hr", status_code=303)


@app.get("/people", response_class=HTMLResponse)
async def page_people(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    rows = await repo.list_public_profiles(pool, limit=300)
    visible = [r for r in rows if _github_ok(str(r["github_url"]))]
    return _templates.TemplateResponse(
        request,
        "people.html",
        {"title": "Люди", "nav": "people", "profiles": visible, "uid": uid},
    )


@app.get("/people/{telegram_user_id}", response_class=HTMLResponse)
async def page_person(telegram_user_id: int, request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    prof = await repo.get_member_profile(pool, telegram_user_id)
    if not prof:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return _templates.TemplateResponse(
        request,
        "person.html",
        {
            "title": prof.get("display_name") or f"Участник {telegram_user_id}",
            "nav": "people",
            "p": prof,
            "pid": telegram_user_id,
            "uid": uid,
        },
    )


@app.get("/me", response_class=HTMLResponse)
async def page_me(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    await repo.ensure_member_profile_row(pool, uid)
    prof = await repo.get_member_profile(pool, uid)
    return _templates.TemplateResponse(
        request,
        "profile_edit.html",
        {"title": "Мой профиль", "nav": "me", "p": prof, "uid": uid, "github_ok": _github_ok},
    )


@app.post("/me")
async def page_me_save(
    request: Request,
    pool=Depends(pool_dep),
    display_name: str = Form(""),
    bio: str = Form(""),
    github_url: str = Form(""),
    clear_resume: str | None = Form(None),
    resume: UploadFile | None = File(None),
    photos: list[UploadFile] = File(default_factory=list),
):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    g = github_url.strip()
    if not _github_ok(g):
        return _templates.TemplateResponse(
            request,
            "profile_edit.html",
            {
                "title": "Мой профиль",
                "nav": "me",
                "p": await repo.get_member_profile(pool, uid),
                "uid": uid,
                "github_ok": _github_ok,
                "error": "Укажите ссылку вида https://github.com/username",
            },
            status_code=400,
        )

    settings = get_settings()
    max_b = settings.web_max_profile_photo_mb * 1024 * 1024
    allowed_mime = {"image/jpeg", "image/png", "image/webp"}
    rel_paths: list[str] = []

    if photos:
        if len(photos) > 3:
            return _templates.TemplateResponse(
                request,
                "profile_edit.html",
                {
                    "title": "Мой профиль",
                    "nav": "me",
                    "p": await repo.get_member_profile(pool, uid),
                    "uid": uid,
                    "github_ok": _github_ok,
                    "error": "Не больше трёх фото",
                },
                status_code=400,
            )
        user_dir = profile_root() / str(uid)
        user_dir.mkdir(parents=True, exist_ok=True)
        for up in photos:
            if not up.filename:
                continue
            raw = await up.read()
            if len(raw) > max_b:
                return _templates.TemplateResponse(
                    request,
                    "profile_edit.html",
                    {
                        "title": "Мой профиль",
                        "nav": "me",
                        "p": await repo.get_member_profile(pool, uid),
                        "uid": uid,
                        "github_ok": _github_ok,
                        "error": f"Файл слишком большой (макс. {settings.web_max_profile_photo_mb} МБ)",
                    },
                    status_code=400,
                )
            ct = (up.content_type or "").split(";")[0].strip().lower()
            if ct not in allowed_mime:
                return _templates.TemplateResponse(
                    request,
                    "profile_edit.html",
                    {
                        "title": "Мой профиль",
                        "nav": "me",
                        "p": await repo.get_member_profile(pool, uid),
                        "uid": uid,
                        "github_ok": _github_ok,
                        "error": "Только JPEG, PNG или WebP",
                    },
                    status_code=400,
                )
            if ct == "image/jpeg":
                ext = "jpg"
            elif ct == "image/png":
                ext = "png"
            else:
                ext = "webp"
            fn = f"{uuid.uuid4().hex}.{ext}"
            dest = user_dir / fn
            dest.write_bytes(raw)
            rel_paths.append(f"{uid}/{fn}")

    existing = await repo.get_member_profile(pool, uid)
    old_photos = _valid_photo_paths(existing.get("photo_paths") if existing else None)

    final_photos = _valid_photo_paths(rel_paths) if rel_paths else old_photos

    resume_relpath: str | None = None
    if existing and existing.get("resume_path"):
        rp = str(existing["resume_path"]).strip().replace("\\", "/")
        resume_relpath = rp if rp and ".." not in rp else None

    if clear_resume == "1":
        if resume_relpath:
            try:
                old_abs = _safe_under(profile_root().resolve(), resume_relpath)
                if old_abs and old_abs.is_file():
                    old_abs.unlink()
            except Exception:
                log.warning("remove old resume failed", exc_info=True)
        resume_relpath = None

    resume_up = resume
    if resume_up and resume_up.filename:
        max_r = settings.web_max_resume_mb * 1024 * 1024
        body = await resume_up.read()
        if len(body) > max_r:
            return _templates.TemplateResponse(
                request,
                "profile_edit.html",
                {
                    "title": "Мой профиль",
                    "nav": "me",
                    "p": await repo.get_member_profile(pool, uid),
                    "uid": uid,
                    "github_ok": _github_ok,
                    "error": f"Резюме слишком большое (макс. {settings.web_max_resume_mb} МБ)",
                },
                status_code=400,
            )
        ct = (resume_up.content_type or "").split(";")[0].strip().lower()
        if ct != "application/pdf" and not (resume_up.filename or "").lower().endswith(".pdf"):
            return _templates.TemplateResponse(
                request,
                "profile_edit.html",
                {
                    "title": "Мой профиль",
                    "nav": "me",
                    "p": await repo.get_member_profile(pool, uid),
                    "uid": uid,
                    "github_ok": _github_ok,
                    "error": "Резюме только в формате PDF",
                },
                status_code=400,
            )
        user_dir = profile_root() / str(uid)
        user_dir.mkdir(parents=True, exist_ok=True)
        dest = user_dir / "resume.pdf"
        dest.write_bytes(body)
        resume_relpath = f"{uid}/resume.pdf"

    await repo.upsert_member_profile(
        pool,
        uid,
        display_name=display_name.strip() or None,
        bio=bio.strip() or None,
        github_url=g,
        photo_paths=final_photos,
        resume_path=resume_relpath,
    )
    return RedirectResponse("/me", status_code=303)
