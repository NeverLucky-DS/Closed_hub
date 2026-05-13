from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from pypdf import PdfReader

from db import repo
from services import llm
from services.file_storage import profile_root


class ProfileAnalysisError(Exception):
    pass


def _extract_pdf_text(path: Path, max_chars: int = 22000) -> str:
    """Достаёт текст из PDF для анализа профиля."""
    reader = PdfReader(str(path))
    parts: list[str] = []
    total = 0
    for page in reader.pages[:40]:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        if text:
            parts.append(text)
            total += len(text)
        if total >= max_chars:
            break
    return "\n".join(parts)[:max_chars]


def _safe_profile_path(rel_path: str | None) -> Path | None:
    rel = (rel_path or "").strip().replace("\\", "/")
    if not rel or ".." in rel:
        return None
    root = profile_root().resolve()
    full = (root / rel).resolve()
    if root not in full.parents:
        return None
    return full if full.is_file() else None


def _github_username(github_url: str | None) -> str | None:
    parsed = urlparse((github_url or "").strip().rstrip("/"))
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) != 1:
        return None
    username = parts[0].strip()
    if not username or username.startswith("-") or username.endswith("-"):
        return None
    return username


def _repo_line(item: dict[str, Any]) -> str:
    name = str(item.get("name") or "").strip()
    desc = str(item.get("description") or "").strip()
    lang = str(item.get("language") or "").strip()
    stars = int(item.get("stargazers_count") or 0)
    topics = item.get("topics") or []
    tags = ", ".join(str(t) for t in topics[:5]) if isinstance(topics, list) else ""
    bits = [name]
    if lang:
        bits.append(f"language: {lang}")
    bits.append(f"stars: {stars}")
    if tags:
        bits.append(f"topics: {tags}")
    if desc:
        bits.append(f"description: {desc[:240]}")
    return " | ".join(bits)


async def _fetch_github_public(username: str) -> tuple[str, dict[str, Any]]:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "ClosedHubProfileAnalysis/1.0",
    }
    timeout = httpx.Timeout(12.0, connect=5.0)
    base = "https://api.github.com"
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            user_resp = await client.get(f"{base}/users/{username}")
            if user_resp.status_code == 404:
                raise ProfileAnalysisError("GitHub-профиль не найден.")
            if user_resp.status_code == 403:
                raise ProfileAnalysisError("GitHub временно ограничил публичные запросы. Попробуйте позже.")
            user_resp.raise_for_status()
            user = user_resp.json()

            repos_resp = await client.get(
                f"{base}/users/{username}/repos",
                params={"type": "owner", "sort": "updated", "per_page": 40},
            )
            if repos_resp.status_code == 403:
                raise ProfileAnalysisError("GitHub временно ограничил публичные запросы к репозиториям.")
            repos_resp.raise_for_status()
            repos = repos_resp.json()
    except ProfileAnalysisError:
        raise
    except httpx.TimeoutException as e:
        raise ProfileAnalysisError("GitHub не ответил за отведённое время. Попробуйте позже.") from e
    except httpx.HTTPError as e:
        raise ProfileAnalysisError("Не удалось получить публичные данные GitHub.") from e

    public_repos = [r for r in repos if not r.get("fork")]
    public_repos.sort(key=lambda r: int(r.get("stargazers_count") or 0), reverse=True)
    used_repos = public_repos[:12]

    lines = [
        f"username: {username}",
        f"name: {user.get('name') or ''}",
        f"bio: {user.get('bio') or ''}",
        f"company: {user.get('company') or ''}",
        f"location: {user.get('location') or ''}",
        f"public_repos: {user.get('public_repos') or 0}",
        f"followers: {user.get('followers') or 0}",
        "repositories:",
    ]
    lines.extend(f"- {_repo_line(item)}" for item in used_repos)

    source_info = {
        "github_username": username,
        "github_public_repos_found": len(public_repos),
        "github_repos_used": len(used_repos),
        "github_api": "public REST API without OAuth",
    }
    return "\n".join(lines), source_info


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()][:12]
    if isinstance(raw, str) and raw.strip():
        return [raw.strip()]
    return []


def _normalize_result(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "career_summary": str(raw.get("career_summary") or "").strip(),
        "stack": _as_list(raw.get("stack")),
        "strengths": _as_list(raw.get("strengths")),
        "projects": _as_list(raw.get("projects")),
        "resume_recommendations": _as_list(raw.get("resume_recommendations")),
        "about_block": str(raw.get("about_block") or "").strip(),
        "questions_for_user": _as_list(raw.get("questions_for_user")),
    }


async def run_profile_analysis(pool, user_id: int, profile: Any) -> dict[str, Any]:
    """Запускает приватный анализ резюме и публичного GitHub для владельца профиля."""
    resume_rel = str(profile.get("resume_path") or "").strip() if profile else ""
    github_url = str(profile.get("github_url") or "").strip() if profile else ""
    source_info: dict[str, Any] = {"mode": "sync_button"}
    await repo.start_profile_analysis(
        pool,
        user_id=user_id,
        source_resume_path=resume_rel or None,
        source_github_url=github_url or None,
        source_info=source_info,
    )

    try:
        resume_path = _safe_profile_path(resume_rel)
        if resume_path is None:
            raise ProfileAnalysisError("Сначала загрузите PDF-резюме в профиле.")
        username = _github_username(github_url)
        if username is None:
            raise ProfileAnalysisError("Укажите GitHub вида https://github.com/username.")

        resume_text = _extract_pdf_text(resume_path)
        if not resume_text.strip():
            resume_text = "(из PDF не удалось извлечь текст; опирайся на GitHub и попроси пользователя добавить данные)"

        github_block, github_info = await _fetch_github_public(username)
        source_info.update(github_info)
        source_info["resume_chars"] = len(resume_text)

        result = _normalize_result(await llm.analyze_profile(pool, resume_text, github_block))
        await repo.finish_profile_analysis(
            pool,
            user_id=user_id,
            result=result,
            source_info=source_info,
        )
        return result
    except ProfileAnalysisError as e:
        await repo.fail_profile_analysis(pool, user_id=user_id, error=str(e), source_info=source_info)
        raise
    except Exception as e:
        await repo.fail_profile_analysis(
            pool,
            user_id=user_id,
            error="Анализ не завершился из-за внутренней ошибки. Попробуйте позже.",
            source_info=source_info,
        )
        raise ProfileAnalysisError("Анализ не завершился из-за внутренней ошибки. Попробуйте позже.") from e
