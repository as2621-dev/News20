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
