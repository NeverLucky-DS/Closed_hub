from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.templating import Jinja2Templates

from config import get_settings
from db import repo
from db.pool import close_pool, create_pool
from db.schema_patch import apply_pending_patches
from services.file_storage import library_root, profile_root

log = logging.getLogger(__name__)

_templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent / "templates"))
_static_dir = Path(__file__).resolve().parent / "static"

_auth_rl: dict[str, float] = {}
_RL_WINDOW_SEC = 55.0


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


def _event_cover_class(title: str | None) -> str:
    base = (title or "").strip() or "?"
    h = hashlib.md5(base.encode("utf-8")).digest()[0]
    return f"cover-{(h % 6) + 1}"


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


def _avatar_class(seed: str | int) -> str:
    h = hashlib.md5(str(seed).encode("utf-8")).digest()[0]
    return f"cover-{(h % 6) + 1}"


def _format_dt_short(dt) -> str:
    if not dt:
        return ""
    try:
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return ""


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
_templates.env.filters["event_cover"] = _event_cover_class
_templates.env.filters["file_kind"] = lambda r: _file_kind(r.get("mime_type"), r.get("original_filename"))
_templates.env.filters["initial"] = _initial_for
_templates.env.filters["avatar_cls"] = _avatar_class
_templates.env.filters["dt_short"] = _format_dt_short
_templates.env.filters["ru_plural"] = _ru_plural
_templates.env.filters["event_badges"] = _event_badges


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool = await create_pool()
    await apply_pending_patches(pool)
    app.state.pool = pool
    profile_root().mkdir(parents=True, exist_ok=True)
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


def _safe_under(root: Path, rel: str) -> Path | None:
    try:
        candidate = (root / rel).resolve()
        root_r = root.resolve()
        if not str(candidate).startswith(str(root_r)):
            return None
        return candidate
    except Exception:
        return None


@app.get("/api/library/{file_id}/raw")
async def library_file_raw(
    file_id: int,
    request: Request,
    pool=Depends(pool_dep),
    _uid: int = Depends(require_uid_api),
):
    row = await repo.get_file_record(pool, file_id)
    if not row or row["status"] != "confirmed":
        raise HTTPException(status_code=404)
    path = Path(row["storage_path"])
    if not path.is_absolute():
        path = Path.cwd() / path
    path = path.resolve()
    lib = library_root().resolve()
    if not str(path).startswith(str(lib)):
        raise HTTPException(status_code=404)
    if not path.is_file():
        raise HTTPException(status_code=404)
    mime = row.get("mime_type") or "application/octet-stream"
    name = row.get("original_filename") or path.name
    return FileResponse(path, media_type=mime, filename=name)


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
    paths = prof.get("photo_paths") or []
    rel = f"{telegram_user_id}/{filename}"
    ok = any(str(p).replace("\\", "/") == rel for p in paths)
    if not ok:
        raise HTTPException(status_code=404)
    root = profile_root().resolve()
    full = _safe_under(root, rel)
    if not full or not full.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(full)


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
            },
        )

    cat_label = next((c["label_ru"] for c in cats if c["slug"] == cat), cat)
    files = await repo.list_library_files(pool, limit=500, category_slug=cat)
    selected = None
    if file:
        selected = next((f for f in files if int(f["id"]) == int(file)), None)
    if selected is None and files:
        selected = files[0]
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
        },
    )


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
    return _templates.TemplateResponse(
        request,
        "feed.html",
        {
            "title": "Мероприятия",
            "nav": "feed",
            "events": events,
            "metrics": metrics,
            "uid": uid,
        },
    )


@app.get("/today", response_class=HTMLResponse)
async def page_today(request: Request, pool=Depends(pool_dep)):
    uid = session_uid(request)
    if uid is None:
        return RedirectResponse("/login", status_code=302)
    if await repo.member_status(pool, uid) != "active":
        request.session.clear()
        return RedirectResponse("/login", status_code=302)
    strip = await repo.list_events_today_strip(pool, limit=20)
    metrics = _events_metrics(strip)
    return _templates.TemplateResponse(
        request,
        "today.html",
        {
            "title": "Сегодня",
            "nav": "today",
            "events": strip,
            "metrics": metrics,
            "uid": uid,
        },
    )


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
    old_photos: list[str] = []
    if existing and existing.get("photo_paths"):
        old_photos = list(existing["photo_paths"])

    final_photos = rel_paths if rel_paths else old_photos

    await repo.upsert_member_profile(
        pool,
        uid,
        display_name=display_name.strip() or None,
        bio=bio.strip() or None,
        github_url=g,
        photo_paths=final_photos,
    )
    return RedirectResponse("/me", status_code=303)
