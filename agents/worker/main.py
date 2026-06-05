"""Minimal FastAPI worker: the grounded Q&A endpoint (Phase 2b SP2).

Exposes ``POST /api/story/{story_id}/question``: load the story's grounding
corpus (SP1, service-role Supabase read) → answer it grounded in-context
(``agents/qa/agent.py``) → return the typed :class:`~agents.qa.models.QuestionAnswer`.

GUARANTEE — the conversation never breaks (``prototype-port-map.md`` §7): EVERY
failure (missing/empty corpus, over-budget corpus, LLM/parse error, anything
unexpected) returns **HTTP 200** with the graceful refusal payload and logs a
typed ``ErrorResponse`` with ``fix_suggestion`` — never a 5xx. An ungrounded
answer is never surfaced as grounded (Rule 9).

The service-role Supabase client is built the SAME way as the Phase 1d e2e
script (``create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)``) and injected
into ``load_grounding_corpus``; this module reads keys from the environment at
request time (never logged). Per-story corpus **context caching** lives in
``agents/worker/corpus_cache.py``.

SP4 (cache) wraps the persisted-turn cache around this same route; SP3 (frontend)
calls this endpoint and maps ``answer_is_grounded`` to the two visual states.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.pipeline.llm_clients import LLMClient
from agents.qa.agent import answer_question, build_refusal_answer
from agents.qa.corpus import load_grounding_corpus
from agents.qa.models import QuestionAnswer, StoryQaCacheRow
from agents.shared.exceptions import GroundingCorpusError
from agents.shared.logger import get_logger
from agents.voice.live_token import EphemeralTokenResponse, mint_ephemeral_token
from agents.worker.corpus_cache import get_or_load_corpus

logger = get_logger("worker.qa")

app = FastAPI(title="News20 Worker", version="2b")

# Reason: the verified-answer cache table (reference/supabase-schema.md story_qa);
# the (qa_story_id, qa_question_text) UNIQUE constraint is the cache key.
STORY_QA_TABLE = "story_qa"

# ── Deploy hardening (CSO follow-ups: phase-2b-2c-m2 MEDIUM-1/2 + phase-3 M-1) ──
# CORS — scope to the app origin via env, NEVER `*` (the worker holds the
# service-role key). QA_API_ALLOWED_ORIGINS is a comma-separated list; the local
# Next.js dev origin is the safe default so dev works without config, but a real
# deploy MUST set it (e.g. the Capacitor app origin / the hosted web origin).
_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get(
        "QA_API_ALLOWED_ORIGINS", "http://localhost:3000"
    ).split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["content-type", "authorization"],
    allow_credentials=False,
)

# Per-IP rate limit on the PAID endpoints (Q&A LLM call + Gemini Live token mint),
# the interim cost-abuse ceiling until per-user auth+quota lands (phase-3b SP4).
# Dependency-free in-memory sliding window — correct for a SINGLE worker instance;
# a multi-instance deploy needs a shared store (Redis) or an authenticated proxy.
_RATE_LIMIT_PER_MINUTE = int(os.environ.get("QA_RATE_LIMIT_PER_MINUTE", "20"))
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMITED_PREFIXES = ("/api/story/", "/api/voice/live-token")
_request_times_by_ip: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    """Best-effort client IP for rate limiting (first X-Forwarded-For hop if proxied)."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


@app.middleware("http")
async def rate_limit_paid_endpoints(request: Request, call_next):
    """Throttle the paid endpoints per IP (sliding 60s window) → HTTP 429 on excess.

    Only the cost-bearing paths are limited; all other routes pass through. A 429
    here is a deliberate, honest throttle (Rule 12) — distinct from the Q&A route's
    HTTP-200 graceful-refusal contract, which covers internal errors, not abuse.
    """
    path = request.url.path
    if request.method == "POST" and path.startswith(_RATE_LIMITED_PREFIXES):
        now = time.monotonic()
        client_ip = _client_ip(request)
        recent = [
            ts
            for ts in _request_times_by_ip.get(client_ip, [])
            if now - ts < _RATE_LIMIT_WINDOW_SECONDS
        ]
        if len(recent) >= _RATE_LIMIT_PER_MINUTE:
            logger.warning(
                "rate_limit_exceeded",
                client_ip=client_ip,
                path=path,
                limit_per_minute=_RATE_LIMIT_PER_MINUTE,
                fix_suggestion="Client exceeded the per-IP rate limit; returned 429.",
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error_code": "rate_limited",
                    "error_message": "Too many requests; slow down.",
                },
                headers={"Retry-After": str(int(_RATE_LIMIT_WINDOW_SECONDS))},
            )
        recent.append(now)
        _request_times_by_ip[client_ip] = recent
    return await call_next(request)


class QuestionRequest(BaseModel):
    """The Q&A request body (``api-contracts.md`` ``QuestionRequest``).

    Attributes:
        question_text: The reader's question.
        conversation_id: Optional multi-turn id (reserved for M3; unused in M2).
    """

    question_text: str = Field(..., min_length=1, description="The reader's question.")
    conversation_id: str | None = Field(
        default=None, description="Reserved for M3 multi-turn; unused in M2."
    )


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (``timestamp_utc``)."""
    return datetime.now(timezone.utc).isoformat()


def _log_error_response(
    *, error_code: str, error_message: str, story_id: str, fix_suggestion: str
) -> None:
    """Log a typed ``ErrorResponse``-shaped record (``api-contracts.md``).

    Args:
        error_code: Stable machine code for the failure class.
        error_message: Human-readable message.
        story_id: The story the request was for.
        fix_suggestion: Actionable remediation hint (CLAUDE.md mandate).
    """
    logger.error(
        error_code,
        error_code=error_code,
        error_message=error_message,
        error_details={"story_id": story_id},
        timestamp_utc=_utc_now_iso(),
        fix_suggestion=fix_suggestion,
    )


def _build_service_role_client() -> Any:
    """Build a service-role Supabase client (same construction as the e2e script).

    Reads ``SUPABASE_URL`` + ``SUPABASE_SERVICE_ROLE_KEY`` from the environment
    at call time (never logged) and returns a ``supabase-py`` client. The
    service-role key bypasses RLS — only the worker reads/writes content
    (``reference/supabase-schema.md`` §6).

    Returns:
        A configured ``supabase.Client``.

    Raises:
        KeyError: If the required env vars are absent (caught by the route and
            turned into a graceful HTTP-200 refusal).
    """
    # Reason: import lazily so importing this module never requires the env vars
    # (keeps the app importable in tests / at build time).
    from supabase import create_client

    return create_client(
        os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"]
    )


def _read_cached_answer(
    supabase_client: Any, story_id: str, question_text: str
) -> QuestionAnswer | None:
    """Look up a cached verified turn in ``story_qa`` for this exact ``(story, question)``.

    The cache key is the ``(qa_story_id, qa_question_text)`` UNIQUE pair. On a HIT
    the cached row (grounded answer OR a verified refusal) is mapped back to a
    :class:`~agents.qa.models.QuestionAnswer` and returned — the endpoint then
    SKIPS the whole LLM + verification round-trip (the SP4 answer-cache win, on
    top of SP2's per-story corpus context cache). On a miss, returns ``None``.

    NEVER raises into the request path: a read failure (network / schema) logs a
    typed ``ErrorResponse`` and returns ``None`` so the endpoint falls through to
    the live answer — the HTTP-200/graceful-fallback contract holds.

    Args:
        supabase_client: The injected service-role Supabase client.
        story_id: The story the question is about (``qa_story_id`` filter).
        question_text: The exact question text (``qa_question_text`` filter).

    Returns:
        The cached :class:`QuestionAnswer` on a hit, else ``None`` (miss OR a
        read failure that falls through to the live answer).
    """
    try:
        response = (
            supabase_client.table(STORY_QA_TABLE)
            .select(
                "qa_story_id,qa_question_text,qa_answer_text,"
                "qa_is_grounded,qa_source_kind,qa_citation_outlet_names"
            )
            .eq("qa_story_id", story_id)
            .eq("qa_question_text", question_text)
            .limit(1)
            .execute()
        )
    except Exception as exc:  # noqa: BLE001 — boundary: never break the response
        _log_error_response(
            error_code="qa_cache_read_failed",
            error_message=str(exc)[:200],
            story_id=story_id,
            fix_suggestion="story_qa cache read failed; falling through to the live answer",
        )
        return None

    rows = getattr(response, "data", None) or []
    if not rows:
        logger.info("qa_cache_miss", story_id=story_id)
        return None

    try:
        cached_row = StoryQaCacheRow.model_validate(rows[0])
    except Exception as exc:  # noqa: BLE001 — boundary: malformed row → live answer
        _log_error_response(
            error_code="qa_cache_row_invalid",
            error_message=str(exc)[:200],
            story_id=story_id,
            fix_suggestion="Cached story_qa row failed validation; falling through to live answer",
        )
        return None

    logger.info(
        "qa_cache_hit", story_id=story_id, qa_is_grounded=cached_row.qa_is_grounded
    )
    return cached_row.to_question_answer()


def _write_cached_answer(
    supabase_client: Any, story_id: str, question_text: str, answer: QuestionAnswer
) -> None:
    """Persist ONE verified turn to ``story_qa`` (service-role write; INSERT only).

    Called after the live answer is produced — on a cache MISS only. Persists
    grounded answers AND verified refusals (a refusal is a cacheable result), with
    ``qa_is_grounded`` preserved from the answer and citation outlet names flattened
    into ``qa_citation_outlet_names`` (``qa_source_kind='rag_cached'``).

    NEVER raises into the request path: a write failure (network / UNIQUE race /
    schema) logs a typed ``ErrorResponse`` and returns — the live answer is still
    returned to the user (the HTTP-200/graceful-fallback contract holds; the cache
    is best-effort).

    Args:
        supabase_client: The injected service-role Supabase client.
        story_id: The story the answer was grounded on (``qa_story_id``).
        question_text: The exact question asked (``qa_question_text``).
        answer: The live :class:`QuestionAnswer` to cache.
    """
    cache_row = StoryQaCacheRow.from_question_answer(
        story_id=story_id, question_text=question_text, answer=answer
    )
    try:
        supabase_client.table(STORY_QA_TABLE).insert(
            cache_row.to_insert_payload()
        ).execute()
    except Exception as exc:  # noqa: BLE001 — boundary: cache write is best-effort
        _log_error_response(
            error_code="qa_cache_write_failed",
            error_message=str(exc)[:200],
            story_id=story_id,
            fix_suggestion="story_qa cache write failed; answer still returned (cache is best-effort)",
        )
        return
    logger.info(
        "qa_cache_written",
        story_id=story_id,
        qa_is_grounded=cache_row.qa_is_grounded,
        citation_outlet_count=len(cache_row.qa_citation_outlet_names),
    )


@app.post("/api/story/{story_id}/question")
async def post_story_question(
    story_id: str, request: QuestionRequest
) -> QuestionAnswer:
    """Answer a reader's question grounded ONLY in the story's source corpus.

    On ANY failure returns HTTP 200 + the graceful refusal payload (never a 5xx)
    and logs a typed ``ErrorResponse`` with ``fix_suggestion`` so the
    conversation never breaks (``prototype-port-map.md`` §7).

    Args:
        story_id: The story to ground the answer on (path param).
        request: The :class:`QuestionRequest` body.

    Returns:
        A :class:`QuestionAnswer` — grounded with citations, or the refusal
        payload (``answer_is_grounded=False``) on any error / off-source question.
    """
    logger.info(
        "qa_request_received",
        story_id=story_id,
        question_length=len(request.question_text),
    )
    try:
        supabase_client = _build_service_role_client()
    except KeyError as exc:
        _log_error_response(
            error_code="qa_supabase_config_missing",
            error_message=f"missing Supabase env var: {exc}",
            story_id=story_id,
            fix_suggestion="Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the worker env",
        )
        return build_refusal_answer()

    # Reason: SP4 answer cache — a verified turn for this EXACT (story, question)
    # is served straight from story_qa, skipping the whole LLM + verification
    # round-trip. Layers ON TOP of the per-story corpus context cache below.
    cached_answer = _read_cached_answer(
        supabase_client, story_id, request.question_text
    )
    if cached_answer is not None:
        return cached_answer

    try:
        # Reason: cached per-story so repeat questions about the same story skip
        # the Supabase reads + corpus assembly (the SP4 answer cache layers on top).
        corpus = get_or_load_corpus(
            story_id=story_id,
            supabase_client=supabase_client,
            loader=load_grounding_corpus,
        )
    except GroundingCorpusError as exc:
        # Reason: corpus could not be assembled (no grounding text / over budget) —
        # graceful refusal, NOT a 500 (the boundary fallback the brief mandates).
        _log_error_response(
            error_code="qa_corpus_unavailable",
            error_message=exc.message,
            story_id=story_id,
            fix_suggestion=exc.fix_suggestion,
        )
        return build_refusal_answer()
    except Exception as exc:  # noqa: BLE001 — boundary: never 5xx
        _log_error_response(
            error_code="qa_corpus_load_unexpected",
            error_message=str(exc)[:200],
            story_id=story_id,
            fix_suggestion="Unexpected corpus-load error; check Supabase reachability + schema",
        )
        return build_refusal_answer()

    try:
        live_answer = await answer_question(
            question_text=request.question_text,
            corpus=corpus,
            llm_client=LLMClient(),
        )
    except Exception as exc:  # noqa: BLE001 — boundary: never 5xx
        _log_error_response(
            error_code="qa_answer_unexpected",
            error_message=str(exc)[:200],
            story_id=story_id,
            fix_suggestion="Unexpected answer error; check Gemini key/quota — returned refusal",
        )
        return build_refusal_answer()

    # Reason: persist this verified turn (grounded OR refusal) so the identical
    # (story, question) is served from cache next time. Best-effort: a write
    # failure logs + returns the live answer (never breaks the HTTP-200 contract).
    _write_cached_answer(supabase_client, story_id, request.question_text, live_answer)
    return live_answer


# ---------------------------------------------------------------------------
# Gemini Live ephemeral-token mint (Phase 3 SP3) — additive route.
#
# The frontend (``src/lib/voice/useGeminiLive.ts``) opens a raw WebSocket to the
# Gemini Live ``BidiGenerateContentConstrained`` endpoint. To keep GEMINI_API_KEY
# off-device, the client first POSTs here; the worker mints a single-use token
# (the key stays server-side, never logged/returned) and hands back ONLY the
# ``auth_tokens/...`` name, which the client passes via ``?access_token=``.
# ---------------------------------------------------------------------------


@app.post("/api/voice/live-token")
async def post_voice_live_token() -> EphemeralTokenResponse:
    """Mint a single-use Gemini Live ephemeral token (key stays server-side).

    Delegates to :func:`agents.voice.live_token.mint_ephemeral_token`, which calls
    ``POST v1alpha/auth_tokens`` with the ``x-goog-api-key`` header and returns the
    minted ``auth_tokens/...`` name. The long-lived ``GEMINI_API_KEY`` is NEVER
    sent to the client and NEVER logged (env-var-safety mandate).

    On a mint failure (missing key / Gemini error / malformed response) this
    returns HTTP 502 — NOT the Q&A endpoint's graceful HTTP-200 refusal — because
    a missing token has no in-conversation fallback: the client cannot open the
    WSS at all, so the honest signal is an explicit error (Rule 12 — fail loud).

    Returns:
        An :class:`EphemeralTokenResponse` whose ``ephemeral_token_name`` starts
        with ``auth_tokens/``.

    Raises:
        HTTPException: HTTP 502 when the token could not be minted.
    """
    logger.info("live_token_request_received")
    try:
        token = await mint_ephemeral_token()
    except RuntimeError as exc:
        logger.error(
            "live_token_mint_failed",
            error_code="live_token_mint_failed",
            error_message=str(exc)[:200],
            timestamp_utc=_utc_now_iso(),
            fix_suggestion="Confirm GEMINI_API_KEY is set + has Live API access; check network",
        )
        raise HTTPException(
            status_code=502,
            detail="Could not mint a Gemini Live ephemeral token",
        ) from exc
    return token
