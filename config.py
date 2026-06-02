from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DOWNLOADS_DIR = BASE_DIR / "downloads"
OUTPUTS_DIR = BASE_DIR / "outputs"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    telegram_bot_token: str | None = Field(default=None, alias="TELEGRAM_BOT_TOKEN")
    telegram_webhook_secret: str | None = Field(default=None, alias="TELEGRAM_WEBHOOK_SECRET")

    instagram_access_token: str | None = Field(default=None, alias="INSTAGRAM_ACCESS_TOKEN")
    instagram_user_id: str | None = Field(default=None, alias="INSTAGRAM_USER_ID")
    graph_api_version: str = Field(default="v24.0", alias="GRAPH_API_VERSION")
    instagram_api_host: Literal["auto", "facebook", "instagram"] = Field(
        default="auto",
        alias="INSTAGRAM_API_HOST",
    )

    storage_provider: Literal["cloudflare_r2", "supabase"] = Field(
        default="cloudflare_r2",
        alias="STORAGE_PROVIDER",
    )
    cloudflare_r2_access_key_id: str | None = Field(
        default=None,
        alias="CLOUDFLARE_R2_ACCESS_KEY_ID",
    )
    cloudflare_r2_secret_access_key: str | None = Field(
        default=None,
        alias="CLOUDFLARE_R2_SECRET_ACCESS_KEY",
    )
    cloudflare_r2_bucket: str | None = Field(default=None, alias="CLOUDFLARE_R2_BUCKET")
    cloudflare_r2_endpoint: str | None = Field(default=None, alias="CLOUDFLARE_R2_ENDPOINT")
    cloudflare_r2_public_url: str | None = Field(default=None, alias="CLOUDFLARE_R2_PUBLIC_URL")

    supabase_url: str | None = Field(default=None, alias="SUPABASE_URL")
    supabase_service_role_key: str | None = Field(default=None, alias="SUPABASE_SERVICE_ROLE_KEY")
    supabase_bucket: str | None = Field(default=None, alias="SUPABASE_BUCKET")

    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    gemini_api_key: str | None = Field(default=None, alias="GEMINI_API_KEY")
    gemini_image_model: str = Field(default="gemini-2.5-flash-image", alias="GEMINI_IMAGE_MODEL")
    gemini_image_aspect_ratio: str = Field(default="9:16", alias="GEMINI_IMAGE_ASPECT_RATIO")
    gemini_image_size: str | None = Field(default=None, alias="GEMINI_IMAGE_SIZE")
    enable_ai_prompt_visuals: bool = Field(default=True, alias="ENABLE_AI_PROMPT_VISUALS")
    public_base_url: str | None = Field(default=None, alias="PUBLIC_BASE_URL")

    database_url: str = Field(default=f"sqlite:///{DATA_DIR / 'bot.db'}", alias="DATABASE_URL")
    whisper_model: str = Field(default="tiny", alias="WHISPER_MODEL")
    enable_transcription: bool = Field(default=True, alias="ENABLE_TRANSCRIPTION")
    low_memory_mode: bool = Field(default=False, alias="LOW_MEMORY_MODE")
    youtube_cookies_file: str | None = Field(default=None, alias="YOUTUBE_COOKIES_FILE")
    youtube_cookies_content: str | None = Field(default=None, alias="YOUTUBE_COOKIES_CONTENT")
    min_reel_seconds: int = Field(default=15, alias="MIN_REEL_SECONDS")
    max_reel_seconds: int = Field(default=45, alias="MAX_REEL_SECONDS")
    prompt_reel_seconds: int = Field(default=25, alias="PROMPT_REEL_SECONDS")
    prompt_visual_count: int = Field(default=3, alias="PROMPT_VISUAL_COUNT")
    max_download_mb: int = Field(default=500, alias="MAX_DOWNLOAD_MB")
    max_concurrent_jobs: int = Field(default=1, alias="MAX_CONCURRENT_JOBS")
    webhook_path: str = Field(default="/telegram-webhook", alias="TELEGRAM_WEBHOOK_PATH")

    @property
    def webhook_url(self) -> str | None:
        if not self.public_base_url:
            return None
        return f"{self.public_base_url.rstrip('/')}{self.webhook_path}"

    @property
    def max_download_bytes(self) -> int:
        return self.max_download_mb * 1024 * 1024


def ensure_directories() -> None:
    for directory in (DATA_DIR, DOWNLOADS_DIR, OUTPUTS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    ensure_directories()
    return Settings()
