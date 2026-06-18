"""Application settings loaded from environment variables via pydantic-settings.

Ported from TLDW (`agents/shared/settings.py`) and trimmed to the M0
quality-spike surface: this spike only needs the Gemini TTS credentials, so
the Pinecone / OpenAI / Supabase fields TLDW requires are intentionally
dropped (Rule 2 — minimum code). ``extra="ignore"`` means the shared project
``.env`` (which also carries unrelated keys) still loads cleanly.

All API keys use SecretStr to prevent accidental logging of sensitive values.
Load from .env file in project root for local development.

Example:
    >>> from agents.shared.settings import Settings
    >>> settings = Settings()
    >>> settings.resolved_gemini_tts_key()  # str, never logged
    '...'
"""

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """News20 agent configuration loaded from environment variables.

    All API keys are stored as SecretStr to prevent accidental exposure in
    logs or error messages. Use ``.get_secret_value()`` (or the
    ``resolved_gemini_tts_key`` helper) to access the actual key value only at
    the API-call boundary.

    Attributes:
        gemini_api_key: Google Gemini API key — used for multi-speaker TTS.
        gemini_api_key_tts: Optional dedicated TTS-permissioned Gemini key.
            Falls back to gemini_api_key when empty.
        youtube_api_key: Optional YouTube Data API v3 key for the catalog seeder.
        log_level: Minimum log level for the structured JSON logger.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Google Gemini API key — used for multi-speaker TTS rendering",
    )
    gemini_api_key_tts: SecretStr = Field(
        default=SecretStr(""),
        description="Optional dedicated Gemini API key with TTS permissions. Falls back to gemini_api_key when empty.",
    )
    serper_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="Serper.dev API key — Google Images SERP search for the poster-seed pipeline.",
    )
    youtube_api_key: str | None = Field(
        default=None,
        description="YouTube Data API v3 key — used by the catalog seeder "
        "(scripts/seed_catalog) to resolve channel handles → channel id + "
        "thumbnail + subscriber count. Optional so existing envs without it "
        "still load; the seeder fails loud at run time when it is missing.",
    )
    youtube_cookiefile: str | None = Field(
        default=None,
        description="Path to a Netscape-format cookies.txt for yt-dlp transcript "
        "fetches (agents/ingestion/adapters/youtube.py). Makes caption downloads "
        "look like a signed-in human, defeating YouTube's 'confirm you're not a "
        "bot' / 429 throttle that blocks datacenter (Railway) IPs. Optional; when "
        "empty yt-dlp runs anonymously (works locally, throttled in the cloud). "
        "Mutually preferred over youtube_cookies_from_browser when both are set.",
    )
    youtube_cookies_from_browser: str | None = Field(
        default=None,
        description="Browser name yt-dlp pulls cookies from (e.g. 'chrome', "
        "'safari', 'firefox') as an alternative to youtube_cookiefile — handy for "
        "local runs on a machine with a logged-in browser. Ignored when "
        "youtube_cookiefile is set. Empty by default (anonymous yt-dlp).",
    )
    youtube_pace_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Base delay (seconds) the YouTube adapter waits between "
        "successive network calls — between channels (RSS) and between videos "
        "(yt-dlp). Self-throttles so polling N followed channels back-to-back from "
        "one IP does not trip YouTube's rate limiter (the '1/7 channels' failure). "
        "0 (default) disables pacing — set ~2.0 for cloud runs.",
    )
    youtube_pace_jitter_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Random extra delay (0..this, seconds) added on top of "
        "youtube_pace_seconds before each paced call, so requests are not evenly "
        "spaced (harder to fingerprint as a bot). 0 (default) = no jitter; ~1.5 "
        "pairs well with youtube_pace_seconds=2.0.",
    )
    xai_api_key: SecretStr = Field(
        default=SecretStr(""),
        description="xAI / Grok API key (XAI_API_KEY) — used by the X account "
        "source adapter (Phase 5d SP2) for Live Search discovery of a followed "
        "handle's recent posts. Empty by default so existing envs still load; the "
        "adapter returns a clean failed status when it is missing. Never logged.",
    )
    pipeline_trigger_secret: SecretStr = Field(
        default=SecretStr(""),
        description="Shared bearer token guarding the worker's pipeline HTTP seam "
        "(POST /pipeline/daily, POST /feed/assemble-for-user). Empty by default so "
        "existing envs still load; the worker refuses to serve those endpoints (500) "
        "until it is set. Never logged.",
    )
    log_level: str = Field(
        default="INFO",
        description="Minimum log level for the structured JSON logger (DEBUG | INFO | WARNING | ERROR)",
    )

    def resolved_gemini_tts_key(self) -> str:
        """Resolve the Gemini key to use for TTS calls.

        Prefers the dedicated TTS key when set, otherwise falls back to the
        main Gemini key. Mirrors the TLDW resolution order.

        Returns:
            The resolved key string. Never log this value.
        """
        tts_key = self.gemini_api_key_tts.get_secret_value().strip()
        if tts_key:
            return tts_key
        return self.gemini_api_key.get_secret_value()
