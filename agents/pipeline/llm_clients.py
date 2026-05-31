"""Minimal Gemini text client for the News20 produce-gate + script pipeline (Phase 1d SP2).

ADAPTED from the TLDW donor (`agents/pipeline/llm_clients.py`). The donor's
client routes traffic to **both** Gemini and OpenAI (embeddings, ranking
failover, Whisper alignment) and includes a multi-speaker TTS method. News20's
SP2 stages — scripting and verification — only need **Gemini text generation**:

    - OpenAI is dropped (the package isn't even installed in the agent venv;
      News20 has no OpenAI dependency — Rule 2, avoid dead code + a hard import).
    - The multi-speaker TTS method is dropped (SP3 reuses M0's
      ``agents/voice/gemini_tts.py`` for the actual audio render).
    - The Google-Search grounding method is dropped: News20 verification grounds
      **in-context** against the single source's body text, not the web
      (memory: news20-qa-incontext-grounding).

What is kept verbatim from the donor: the lazy client init, the
exponential-backoff retry wrapper, and the structured JSON logging with token
lengths + latency + ``fix_suggestion`` on errors.

The Gemini SDK is mocked at this boundary in every SP2 test — no live call, no
cost (CLAUDE.md mocking mandate).

Example:
    >>> from agents.pipeline.llm_clients import LLMClient
    >>> client = LLMClient()
    >>> text = await client.call_gemini("Write the dialogue.", system="You are a writer.")  # doctest: +SKIP
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from agents.shared.exceptions import PipelineStageError
from agents.shared.logger import get_logger
from agents.shared.settings import Settings

logger = get_logger("pipeline.llm_clients")

# Reason: retry knobs tuned for transient LLM API errors (429 rate limit,
# 500/503). 3 attempts with 2^n backoff covers ~14s total wait — ported from
# the donor.
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 2.0
DEFAULT_TIMEOUT_SECONDS = 60

# Reason: News20 SP2 text generation pins Gemini 2.5 Flash — the donor's proven
# scripting/verification model. (The TTS pin `gemini-2.5-flash-preview-tts` in
# reference/stack-notes.md is a different model used by SP3's reused M0 TTS.)
DEFAULT_GEMINI_TEXT_MODEL = "gemini-2.5-flash"


class LLMClient:
    """Minimal async Gemini text client for the SP2 scripting + verification stages.

    Accepts API keys via a ``Settings`` instance (or constructs a default one).
    ``call_gemini`` is async and returns the model's text response as a plain
    string. The underlying ``google.genai`` client is created lazily so importing
    this module never triggers a network/auth call (and tests can patch it).

    Attributes:
        settings: Application settings carrying the Gemini API key.
        max_retries: Maximum retry attempts per call.
        backoff_base_seconds: Base delay for exponential backoff.
        timeout_seconds: Per-request timeout in seconds (reserved for callers).

    Example:
        >>> client = LLMClient(settings=Settings())
        >>> result = await client.call_gemini("Summarize this.", system="You summarize.")  # doctest: +SKIP
    """

    def __init__(
        self,
        settings: Settings | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.settings = settings or Settings()
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.timeout_seconds = timeout_seconds

        # Reason: initialize the provider client lazily to avoid import-time
        # auth/API calls and to keep this importable without a real key.
        self._gemini_client: Any | None = None

    def _get_gemini_client(self) -> Any:
        """Get or create the Google GenAI text client.

        Resolves the API key as ``gemini_api_key`` first, then
        ``gemini_api_key_tts`` as a fallback (mirroring the donor + the M0
        settings resolution order), so a deployment that mints a single
        dual-scope key works with either env var set.

        Returns:
            A configured ``google.genai.Client``.
        """
        if self._gemini_client is None:
            # Reason: import lazily so this module imports cleanly even if the
            # SDK is absent in an environment that never makes a live call.
            from google import genai

            primary_key = self.settings.gemini_api_key.get_secret_value().strip()
            if not primary_key:
                primary_key = self.settings.gemini_api_key_tts.get_secret_value()
            self._gemini_client = genai.Client(api_key=primary_key)
        return self._gemini_client

    async def _retry_with_backoff(self, provider_name: str, call_fn: Any) -> Any:
        """Execute an async callable with exponential-backoff retry.

        Args:
            provider_name: Provider label for logging (e.g., "gemini").
            call_fn: An async callable performing the actual API request.

        Returns:
            The result of the first successful call.

        Raises:
            PipelineStageError: When all retries are exhausted.
        """
        last_exception: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                return await call_fn()
            except Exception as exc:  # noqa: BLE001 — retried + re-raised below
                last_exception = exc
                wait_seconds = self.backoff_base_seconds**attempt
                logger.warning(
                    "llm_call_retry",
                    provider=provider_name,
                    attempt=attempt,
                    max_retries=self.max_retries,
                    wait_seconds=wait_seconds,
                    error_type=type(exc).__name__,
                    error_message=str(exc)[:200],
                    fix_suggestion=f"Verify {provider_name} API key, quota, and network reachability",
                )
                if attempt < self.max_retries:
                    await asyncio.sleep(wait_seconds)

        # Reason: all retries exhausted — raise a descriptive pipeline error.
        error_message = f"{provider_name} call failed after {self.max_retries} retries: {last_exception}"
        raise PipelineStageError(
            stage="llm_client",
            message=error_message,
            fix_suggestion=f"Check {provider_name} API key, rate limits, and service status",
        )

    async def call_gemini(
        self,
        prompt: str,
        system: str = "",
        model: str = DEFAULT_GEMINI_TEXT_MODEL,
        temperature: float = 0.3,
    ) -> str:
        """Call Google Gemini text generation and return the response text.

        Args:
            prompt: User prompt text.
            system: System prompt / instruction text (empty to omit).
            model: Gemini text model name (defaults to ``gemini-2.5-flash``).
            temperature: Sampling temperature.

        Returns:
            The model's plain-text response (empty string if the model returns none).

        Raises:
            PipelineStageError: When all retries are exhausted.

        Example:
            >>> text = await client.call_gemini("Write a line.", system="You write.")  # doctest: +SKIP
        """
        start_time = time.monotonic()
        logger.info(
            "llm_call_started",
            provider="gemini",
            model=model,
            prompt_length=len(prompt),
            system_length=len(system),
        )

        async def _call() -> str:
            from google.genai import types as genai_types

            client = self._get_gemini_client()
            config = genai_types.GenerateContentConfig(
                system_instruction=system if system else None,
                temperature=temperature,
            )
            response = await client.aio.models.generate_content(
                model=model,
                contents=prompt,
                config=config,
            )
            return response.text or ""

        result = await self._retry_with_backoff("gemini", _call)
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
        logger.info(
            "llm_call_completed",
            provider="gemini",
            model=model,
            elapsed_ms=elapsed_ms,
            response_length=len(result),
        )
        return result
