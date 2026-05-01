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
    database_url: str = "postgresql://closedhub:closedhub@localhost:5432/closedhub"

    # Число -100… или @username публичной супергруппы (как в Telegram Bot API).
    telegram_group_chat_id: Union[int, str, None] = None

    # Темы форума: id из ссылки t.me/c/…/<topic_id>
    telegram_topic_news: Optional[int] = None
    telegram_topic_discussion: Optional[int] = None
    telegram_topic_rating: Optional[int] = None

    # Устаревшее имя: если задано, перекрывает только публикацию мероприятий.
    telegram_events_topic_id: Optional[int] = None

    initial_whitelist_telegram_ids: str = ""

    file_storage_path: str = "./storage"
    max_pdf_size_mb: int = 20

    mistral_model_routing: str = "mistral-small-latest"
    mistral_model_default: str = "mistral-small-latest"

    hr_context_debounce_sec: int = 45

    file_categories: str = "course,contest,notes,other"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
