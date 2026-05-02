from functools import lru_cache
from typing import Any, Optional, Union

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
    )

    telegram_bot_token: str
    mistral_api_key: str
    # Опционально: отдельный ключ для цепочки «новости на сайт» (dedup + саммари), без конкуренции с остальным ботом.
    mistral_api_key_for_site: Optional[str] = None
    database_url: str = "postgresql://closedhub:closedhub@localhost:5432/closedhub"

    # Число -100… или @username публичной супергруппы (как в Telegram Bot API).
    telegram_group_chat_id: Union[int, str, None] = None

    # Темы форума: id из ссылки t.me/c/…/<topic_id>
    telegram_topic_news: Optional[int] = None
    telegram_topic_discussion: Optional[int] = None
    telegram_topic_rating: Optional[int] = None
    # Куда пересылать отобранные ML-материалы (если пусто — TELEGRAM_TOPIC_NEWS)
    telegram_topic_ml_forward: Optional[int] = None

    ml_forward_max_age_days: int = 21
    ml_forward_daily_cap: int = 20

    # Устаревшее имя: если задано, перекрывает только публикацию мероприятий.
    telegram_events_topic_id: Optional[int] = None

    initial_whitelist_telegram_ids: str = ""

    file_storage_path: str = "./storage"
    max_pdf_size_mb: int = 20

    mistral_model_routing: str = "mistral-small-latest"
    mistral_model_default: str = "mistral-small-latest"

    hr_context_debounce_sec: int = 45

    # Легаси: категории теперь в БД (file_categories); строка ниже не используется для кнопок.
    file_categories: str = "course,contest,notes,other"

    groq_api_key: Optional[str] = None

    google_sheet_id: Optional[str] = None
    google_service_account_json_path: Optional[str] = None

    # Веб-хаб (отдельный процесс uvicorn). Для сессий и HMAC кода входа.
    web_session_secret: Optional[str] = None
    web_public_base_url: Optional[str] = None
    web_auth_code_ttl_sec: int = 600
    web_max_profile_photo_mb: int = 3
    web_max_resume_mb: int = 5
    # Через запятую: кто может на сайте удалять файлы и скрывать новости (Telegram user id).
    # Пустая строка в .env отключает всех; если переменная не задана — дефолт ниже (владелец хаба).
    web_admin_telegram_ids: str = "1202549697"

    @field_validator("telegram_group_chat_id", mode="before")
    @classmethod
    def _normalize_chat_id(cls, v: Any) -> Union[int, str, None]:
        if v is None or v == "":
            return None
        if isinstance(v, int):
            return v
        s = str(v).strip().strip('"').strip("'")
        if s.startswith("@"):
            return s
        try:
            return int(s)
        except ValueError:
            return s

    @property
    def events_publish_topic_id(self) -> Optional[int]:
        if self.telegram_events_topic_id is not None:
            return self.telegram_events_topic_id
        return self.telegram_topic_news

    @property
    def ml_forward_publish_topic_id(self) -> Optional[int]:
        if self.telegram_topic_ml_forward is not None:
            return self.telegram_topic_ml_forward
        return self.telegram_topic_news

    @property
    def whitelist_seed_ids(self) -> list[int]:
        raw = self.initial_whitelist_telegram_ids.strip()
        if not raw:
            return []
        out: list[int] = []
        for part in raw.split(","):
            part = part.strip()
            if part:
                out.append(int(part))
        return out

    @property
    def file_category_list(self) -> list[str]:
        return [c.strip() for c in self.file_categories.split(",") if c.strip()]

    @property
    def web_admin_id_set(self) -> frozenset[int]:
        raw = self.web_admin_telegram_ids.strip()
        if raw == "":
            return frozenset()
        out: set[int] = set()
        for part in raw.split(","):
            part = part.strip()
            if part:
                out.add(int(part))
        return frozenset(out)


@lru_cache
def get_settings() -> Settings:
    return Settings()
