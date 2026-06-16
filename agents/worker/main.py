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
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from agents.ingestion.adapters.x_resolver import XHandleParseError, resolve_x_handle
from agents.pipeline.llm_clients import LLMClient
from agents.qa.agent import (
    answer_from_web_only,
    answer_question,
    build_refusal_answer,
)
from agents.qa.corpus import load_grounding_corpus
from agents.qa.models import ConversationTurn, QuestionAnswer, StoryQaCacheRow
from agents.shared.exceptions import GroundingCorpusError
from agents.shared.logger import get_logger
from agents.shared.settings import Settings
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
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["content-type", "authorization"],
    allow_credentials=False,
)

# Per-IP rate limit on the PAID endpoints (Q&A LLM call + Gemini Live token mint),
# the interim cost-abuse ceiling until per-user auth+quota lands (phase-3b SP4).
# Dependency-free in-memory sliding window — correct for a SINGLE worker instance;
# a multi-instance deploy needs a shared store (Redis) or an authenticated proxy.
_RATE_LIMIT_PER_MINUTE = int(os.environ.get("QA_RATE_LIMIT_PER_MINUTE", "20"))
_RATE_LIMIT_WINDOW_SECONDS = 60.0
_RATE_LIMITED_PREFIXES = (
    "/api/story/",
    "/api/voice/live-token",
    "/api/sources/search",
)
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
        conversation_turns: Recent prior thread turns (most-recent-last) for
            follow-up resolution — stateless multi-turn (Bug 3). Presence
            bypasses the ``story_qa`` answer cache (a context-dependent answer
            must not poison the ``(story, question)`` cache key).
    """

    question_text: str = Field(..., min_length=1, description="The reader's question.")
    conversation_id: str | None = Field(
        default=None, description="Reserved for M3 multi-turn; unused in M2."
    )
    conversation_turns: list[ConversationTurn] = Field(
        default_factory=list,
        max_length=12,
        description="Recent prior thread turns, most-recent-last; empty on a first question.",
    )
    web_only: bool = Field(
        default=False,
        description=(
            "When True, skip the corpus answer+verify (and the SP4 answer cache) and "
            "answer from web search only — used by the voice tool whose "
            "corpus-in-context already failed to answer. Default False keeps the typed "
            "Detail-view Q&A path byte-identical."
        ),
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
    # BYPASSED (read AND write) when conversation turns are present (a follow-up's
    # answer depends on the thread) OR when web_only is set (the voice tool's
    # web-search answer is not the corpus-grounded answer the cache key represents,
    # so it must neither be served from nor poison the (story, question) cache).
    has_conversation_context = bool(request.conversation_turns)
    skip_answer_cache = has_conversation_context or request.web_only
    if has_conversation_context:
        logger.info(
            "qa_cache_bypassed_conversation",
            story_id=story_id,
            turn_count=len(request.conversation_turns),
        )
    if request.web_only:
        logger.info("qa_cache_bypassed_web_only", story_id=story_id)
    if not skip_answer_cache:
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
        if request.web_only:
            # Reason: the voice tool only fires AFTER the corpus-in-context failed
            # to answer at the model, so the corpus answer+verify would be wasted
            # work — go straight to the web-search fallback (corpus still loaded
            # above for its relatedness context block).
            live_answer = await answer_from_web_only(
                question_text=request.question_text,
                corpus=corpus,
                llm_client=LLMClient(),
                conversation_turns=request.conversation_turns or None,
            )
        else:
            live_answer = await answer_question(
                question_text=request.question_text,
                corpus=corpus,
                llm_client=LLMClient(),
                conversation_turns=request.conversation_turns or None,
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
    # Context-dependent (conversation) AND web_only answers are NOT cached — see
    # the bypass above.
    if not skip_answer_cache:
        _write_cached_answer(
            supabase_client, story_id, request.question_text, live_answer
        )
    return live_answer


class StoryCorpusResponse(BaseModel):
    """The rendered grounding-corpus context block for a story (voice hybrid SP1).

    Returned by ``GET /api/story/{story_id}/corpus`` so the live-voice client can
    inject the story's whole corpus into its session ``systemInstruction`` and
    answer corpus-answerable questions directly (no Railway hop). The block is the
    same labeled, passage-id-tagged text the typed Q&A prompt grounds on
    (``GroundingCorpus.render_context_block``).

    GRACEFUL CONTRACT: on ANY failure the endpoint returns this with an EMPTY
    ``context_block`` (and ``approx_token_count`` 0) at HTTP 200, so the client
    degrades to tool-only voice rather than breaking the session (mirrors the Q&A
    route; never a 5xx).

    Attributes:
        context_block: The newline-joined ``[<passage_id>] <text>`` block, or ``""``
            when the corpus could not be loaded.
        approx_token_count: The corpus's advisory chars/4 token estimate, or ``0``
            on failure (for client-side prompt-budget logging only).
    """

    context_block: str = Field(
        ...,
        description="The rendered '[<passage_id>] <text>' block, or '' on any failure.",
    )
    approx_token_count: int = Field(
        ...,
        ge=0,
        description="Advisory chars/4 token estimate of the corpus (0 on failure).",
    )


@app.get("/api/story/{story_id}/corpus")
async def get_story_corpus(story_id: str) -> StoryCorpusResponse:
    """Return the story's grounding-corpus context block for in-context voice.

    Reuses the SAME server-side, per-story-cached corpus assembly the Q&A endpoint
    uses (:func:`agents.worker.corpus_cache.get_or_load_corpus` →
    :func:`load_grounding_corpus`), so the corpus lives in ONE place. The client
    injects the returned ``context_block`` into the Live session's
    ``systemInstruction`` to answer corpus-answerable questions directly.

    GRACEFUL boundary (mirrors :func:`post_story_question`): on missing Supabase
    config (``KeyError``), :class:`GroundingCorpusError`, or ANY unexpected error,
    logs a typed ``ErrorResponse`` and returns HTTP 200 with an EMPTY block so the
    client degrades to tool-only voice — NEVER a 5xx.

    This GET is intentionally OUTSIDE the per-IP rate-limit allowlist (the
    middleware only throttles the cost-bearing POST paths) — it is a cheap,
    per-story-cached read with no LLM call.

    Args:
        story_id: The story to load the grounding corpus for (path param).

    Returns:
        A :class:`StoryCorpusResponse` with the rendered context block + token
        count, or an empty block (HTTP 200) on any failure.
    """
    logger.info("corpus_request_received", story_id=story_id)
    try:
        supabase_client = _build_service_role_client()
    except KeyError as exc:
        _log_error_response(
            error_code="corpus_supabase_config_missing",
            error_message=f"missing Supabase env var: {exc}",
            story_id=story_id,
            fix_suggestion="Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in the worker env",
        )
        return StoryCorpusResponse(context_block="", approx_token_count=0)

    try:
        corpus = get_or_load_corpus(
            story_id=story_id,
            supabase_client=supabase_client,
            loader=load_grounding_corpus,
        )
    except GroundingCorpusError as exc:
        _log_error_response(
            error_code="corpus_unavailable",
            error_message=exc.message,
            story_id=story_id,
            fix_suggestion=exc.fix_suggestion,
        )
        return StoryCorpusResponse(context_block="", approx_token_count=0)
    except Exception as exc:  # noqa: BLE001 — boundary: never 5xx, degrade to tool-only
        _log_error_response(
            error_code="corpus_load_unexpected",
            error_message=str(exc)[:200],
            story_id=story_id,
            fix_suggestion="Unexpected corpus-load error; client degrades to tool-only voice",
        )
        return StoryCorpusResponse(context_block="", approx_token_count=0)

    logger.info(
        "corpus_request_completed",
        story_id=story_id,
        approx_token_count=corpus.approx_token_count,
    )
    return StoryCorpusResponse(
        context_block=corpus.render_context_block(),
        approx_token_count=corpus.approx_token_count,
    )


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


# ---------------------------------------------------------------------------
# Source-search endpoint (Phase 5c SP3a) — live "add anyone" search.
#
# The static-export SPA cannot hold the YouTube Data API key, so the live
# external-API source search runs HERE (the same worker surface that already
# holds the Gemini key). Ported from the donor `api/sources/search/route.ts`
# (reuse-map §3): YouTube channels via the 2-step search.list → channels.list
# flow, podcasts via the keyless iTunes Search API. The X-handle path is built
# fresh (`agents/ingestion/adapters/x_resolver.py`). `is_already_added` is NOT
# annotated here — the device has no service-role context for the caller's
# follows; the TS client (`src/lib/sourceSearch.ts`) annotates it against the
# user's RLS-scoped follow set after this returns (mirrors SP1).
# ---------------------------------------------------------------------------

_YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
_ITUNES_SEARCH_BASE = "https://itunes.apple.com/search"
_SOURCE_SEARCH_MAX_RESULTS = 10
_SOURCE_SEARCH_TIMEOUT_SECONDS = 10.0
_ITUNES_USER_AGENT = "News20-Sources/1.0"


class _SourceSearchUnavailable(Exception):
    """Internal signal: the search COULD NOT RUN (missing key / upstream error).

    Distinct from an empty-but-successful search (no matches). The endpoint catches
    this and returns ``search_ok=False`` so the client shows "search unavailable"
    instead of "no results" — the honest distinction the ``search_ok`` field exists
    for (Rule 12). Never escapes the module.
    """


class SourceSearchRequest(BaseModel):
    """The source-search request body (Phase 5c SP3a).

    Attributes:
        query: The free-text search query (channel/podcast name, or an X handle/URL).
        kind: Which axis to search — the worker-side searchable subset of
            ``content_source_type`` (``personality`` is a client-side catalog read,
            not a live external search, so it is excluded here).
    """

    query: str = Field(..., min_length=1, description="Free-text search query.")
    kind: Literal["youtube_channel", "podcast", "x_account"] = Field(
        ..., description="The source axis to search."
    )


class SourceSearchResult(BaseModel):
    """One addable source result (Phase 5c SP3a) — the worker contract.

    Shape mirrors the donor `SourceSearchResult` + the News20 `ContentSource`
    fields the future Add UI needs. ``is_already_added`` is intentionally absent:
    the TS client annotates it against the caller's RLS-scoped follows (the worker
    has no per-user follow context). ``is_pending`` is X-only — true when an
    ``@handle`` is stored as a free-text follow because live X enrichment is not
    wired (open question #3).

    Attributes:
        source_name: Display name (channel/podcast title, or X handle).
        external_id: Stable platform id used for dedup/follow (channel id,
            ``itunes-{collectionId}``, or the lower-cased X handle).
        content_source_type: The axis this result lives on.
        thumbnail_url: Avatar/artwork URL, or ``None``.
        description: Short blurb (channel description, episode count, or handle),
            or ``None``.
        subscriber_count: Follower/subscriber count when the provider exposes it,
            else ``None`` (iTunes/X never expose one here).
        is_pending: X-only — ``True`` when stored as a pending free-text follow.
    """

    source_name: str = Field(..., description="Display name of the source.")
    external_id: str = Field(..., description="Stable platform id for dedup/follow.")
    content_source_type: Literal["youtube_channel", "podcast", "x_account"] = Field(
        ..., description="The source axis this result lives on."
    )
    thumbnail_url: str | None = Field(default=None, description="Avatar/artwork URL.")
    description: str | None = Field(default=None, description="Short blurb.")
    subscriber_count: int | None = Field(
        default=None,
        description="Subscriber/follower count when the provider exposes it.",
    )
    is_pending: bool = Field(
        default=False,
        description="X-only: True when stored as a pending free-text follow (no live enrichment).",
    )


class SourceSearchResponse(BaseModel):
    """The source-search response envelope (Phase 5c SP3a).

    A typed envelope (not a bare list) so a missing-key / upstream failure is an
    HONEST, non-crashing signal the client can show as "search unavailable" rather
    than an empty result set masquerading as "no matches" (Rule 12 — fail loud).

    Attributes:
        results: The addable results (possibly empty — a genuine "no matches").
        search_ok: ``False`` when the search could not run (missing key / upstream
            error); the client distinguishes this from an empty-but-successful search.
    """

    results: list[SourceSearchResult] = Field(
        default_factory=list, description="Addable source results."
    )
    search_ok: bool = Field(
        default=True,
        description="False when the search could not run (missing key / upstream error).",
    )


def _log_source_search_error(
    *, error_code: str, error_message: str, kind: str, fix_suggestion: str
) -> None:
    """Log a typed ErrorResponse-shaped record for a source-search failure.

    Args:
        error_code: Stable machine code for the failure class.
        error_message: Human-readable message (never a secret/key value).
        kind: The source axis being searched.
        fix_suggestion: Actionable remediation hint (CLAUDE.md mandate).
    """
    logger.error(
        error_code,
        error_code=error_code,
        error_message=error_message,
        error_details={"kind": kind},
        timestamp_utc=_utc_now_iso(),
        fix_suggestion=fix_suggestion,
    )


async def _search_youtube_channels(query: str) -> list[SourceSearchResult]:
    """Live YouTube channel search — the donor 2-step (search.list → channels.list).

    Step 1 (``search.list?type=channel``) finds channel ids + snippet thumbnails;
    step 2 (``channels.list?part=snippet,statistics``) enriches subscriber counts
    and high-res thumbnails. A missing key returns ``[]`` and LOGS it (never a
    crash, never a swallowed silence). Step-2 failure falls back to the step-1
    snippets (unenriched) so a partial outage still returns addable channels.

    Args:
        query: The channel-name search query.

    Returns:
        The matching channels as typed results (possibly empty).
    """
    api_key = Settings().youtube_api_key
    if not api_key:
        _log_source_search_error(
            error_code="source_search_youtube_missing_api_key",
            error_message="YOUTUBE_API_KEY is not set in the worker environment",
            kind="youtube_channel",
            fix_suggestion="Set YOUTUBE_API_KEY (YouTube Data API v3) in the worker env to enable channel search.",
        )
        # Reason: a missing key means the search could not RUN — signal
        # unavailability (search_ok=False), NOT an empty "no matches" result.
        raise _SourceSearchUnavailable("YOUTUBE_API_KEY not configured")

    snippets_by_id: dict[str, dict[str, str | None]] = {}
    channel_ids: list[str] = []
    async with httpx.AsyncClient(timeout=_SOURCE_SEARCH_TIMEOUT_SECONDS) as http_client:
        # ── Step 1: search.list → channel ids + snippet thumbnails ──
        try:
            search_response = await http_client.get(
                f"{_YOUTUBE_API_BASE}/search",
                params={
                    "type": "channel",
                    "q": query,
                    "part": "snippet",
                    "maxResults": _SOURCE_SEARCH_MAX_RESULTS,
                    "key": api_key,
                },
            )
            search_response.raise_for_status()
            search_body = search_response.json()
        except Exception as exc:  # noqa: BLE001 — boundary: upstream failure → unavailable, logged
            _log_source_search_error(
                error_code="source_search_youtube_search_failed",
                error_message=str(exc)[:200],
                kind="youtube_channel",
                fix_suggestion="Confirm YOUTUBE_API_KEY has Data API v3 enabled + quota, and YouTube is reachable.",
            )
            # Upstream step-1 failure → the search could not run (unavailable).
            raise _SourceSearchUnavailable("YouTube search.list failed") from exc

        for item in search_body.get("items", []) or []:
            channel_id = (item.get("id") or {}).get("channelId")
            if not channel_id:
                continue
            channel_ids.append(channel_id)
            snippets_by_id[channel_id] = {
                "title": (item.get("snippet") or {}).get("title"),
                "description": (item.get("snippet") or {}).get("description"),
                "thumbnail_url": _pick_youtube_thumbnail(
                    (item.get("snippet") or {}).get("thumbnails")
                ),
            }

        if not channel_ids:
            return []

        # ── Step 2: channels.list → subscriber counts + hi-res thumbnails ──
        try:
            channels_response = await http_client.get(
                f"{_YOUTUBE_API_BASE}/channels",
                params={
                    "id": ",".join(channel_ids),
                    "part": "snippet,statistics",
                    "key": api_key,
                },
            )
            channels_response.raise_for_status()
            channels_body = channels_response.json()
        except Exception as exc:  # noqa: BLE001 — boundary: enrich failure → step-1 snippets
            _log_source_search_error(
                error_code="source_search_youtube_enrich_failed",
                error_message=str(exc)[:200],
                kind="youtube_channel",
                fix_suggestion="channels.list failed; returning unenriched search snippets (no sub counts).",
            )
            return _youtube_snippets_to_results(channel_ids, snippets_by_id)

    return _youtube_channels_to_results(channels_body, snippets_by_id)


def _pick_youtube_thumbnail(thumbnails: dict[str, Any] | None) -> str | None:
    """Pick the highest-resolution available YouTube thumbnail URL (high → default).

    Args:
        thumbnails: The snippet's ``thumbnails`` object, or ``None``.

    Returns:
        The best thumbnail URL, or ``None`` when none is present.
    """
    if not thumbnails:
        return None
    for size in ("high", "medium", "default"):
        url = (thumbnails.get(size) or {}).get("url")
        if url:
            return url
    return None


def _youtube_snippets_to_results(
    channel_ids: list[str], snippets_by_id: dict[str, dict[str, str | None]]
) -> list[SourceSearchResult]:
    """Map step-1 search snippets to results (the unenriched step-2-fallback path).

    Args:
        channel_ids: The channel ids discovered by search.list, in order.
        snippets_by_id: The per-channel title/description/thumbnail from step 1.

    Returns:
        Typed results without subscriber counts (channels lacking a title dropped).
    """
    results: list[SourceSearchResult] = []
    for channel_id in channel_ids:
        snippet = snippets_by_id.get(channel_id) or {}
        title = snippet.get("title")
        if not title:
            continue
        results.append(
            SourceSearchResult(
                source_name=title,
                external_id=channel_id,
                content_source_type="youtube_channel",
                thumbnail_url=snippet.get("thumbnail_url"),
                description=snippet.get("description"),
                subscriber_count=None,
            )
        )
    return results


def _youtube_channels_to_results(
    channels_body: dict[str, Any], snippets_by_id: dict[str, dict[str, str | None]]
) -> list[SourceSearchResult]:
    """Map channels.list items to enriched results (subscriber counts + thumbnails).

    Hidden subscriber counts and non-numeric counts map to ``None`` (never a fake
    zero). Falls back to the step-1 snippet thumbnail when the enriched one is absent.

    Args:
        channels_body: The parsed channels.list response.
        snippets_by_id: The step-1 snippets, for the thumbnail fallback.

    Returns:
        The enriched, typed channel results.
    """
    results: list[SourceSearchResult] = []
    for item in channels_body.get("items", []) or []:
        channel_id = item.get("id") or ""
        statistics = item.get("statistics") or {}
        subscriber_count = _parse_subscriber_count(
            raw_count=statistics.get("subscriberCount"),
            is_hidden=bool(statistics.get("hiddenSubscriberCount")),
        )
        snippet = item.get("snippet") or {}
        thumbnail = _pick_youtube_thumbnail(snippet.get("thumbnails")) or (
            snippets_by_id.get(channel_id) or {}
        ).get("thumbnail_url")
        results.append(
            SourceSearchResult(
                source_name=snippet.get("title") or "",
                external_id=channel_id,
                content_source_type="youtube_channel",
                thumbnail_url=thumbnail,
                description=snippet.get("description"),
                subscriber_count=subscriber_count,
            )
        )
    return results


def _parse_subscriber_count(*, raw_count: str | None, is_hidden: bool) -> int | None:
    """Parse a YouTube subscriberCount string to an int, or ``None`` when unusable.

    Args:
        raw_count: The ``statistics.subscriberCount`` string (may be absent).
        is_hidden: The ``hiddenSubscriberCount`` flag.

    Returns:
        The integer count, or ``None`` when hidden / absent / non-numeric.
    """
    if is_hidden or not raw_count:
        return None
    try:
        return int(raw_count)
    except (TypeError, ValueError):
        return None


async def _search_podcasts(query: str) -> list[SourceSearchResult]:
    """Live podcast search via the keyless iTunes Search API (donor port).

    ``external_id`` is built as ``itunes-{collectionId}`` to match the catalog
    seeder convention (reuse-map §2), so a podcast added here resolves to the same
    ``content_sources`` row the seeder would create. Any failure returns ``[]`` and
    LOGS it (never a crash).

    Args:
        query: The podcast-name search query.

    Returns:
        The matching podcasts as typed results (possibly empty).
    """
    try:
        async with httpx.AsyncClient(
            timeout=_SOURCE_SEARCH_TIMEOUT_SECONDS
        ) as http_client:
            response = await http_client.get(
                _ITUNES_SEARCH_BASE,
                params={
                    "term": query,
                    "media": "podcast",
                    "entity": "podcast",
                    "limit": _SOURCE_SEARCH_MAX_RESULTS,
                },
                headers={"User-Agent": _ITUNES_USER_AGENT},
            )
            response.raise_for_status()
            body = response.json()
    except Exception as exc:  # noqa: BLE001 — boundary: upstream failure → unavailable, logged
        _log_source_search_error(
            error_code="source_search_podcasts_failed",
            error_message=str(exc)[:200],
            kind="podcast",
            fix_suggestion="iTunes Search failed (likely rate-limited or unreachable); back off and retry.",
        )
        # Upstream failure → the search could not run (unavailable, not "no matches").
        raise _SourceSearchUnavailable("iTunes Search failed") from exc

    results: list[SourceSearchResult] = []
    for entry in body.get("results", []) or []:
        collection_id = entry.get("collectionId")
        collection_name = entry.get("collectionName")
        if not collection_id or not collection_name:
            continue
        artwork = (
            entry.get("artworkUrl600")
            or entry.get("artworkUrl100")
            or entry.get("artworkUrl60")
        )
        results.append(
            SourceSearchResult(
                source_name=collection_name,
                external_id=f"itunes-{collection_id}",
                content_source_type="podcast",
                thumbnail_url=artwork,
                description=_podcast_description(entry),
                subscriber_count=None,
            )
        )
    return results


def _podcast_description(entry: dict[str, Any]) -> str | None:
    """Build a podcast blurb from episode count + artist (donor parity).

    Args:
        entry: One iTunes Search result object.

    Returns:
        ``"{n} episodes · {artist}"`` when a track count is present, else the
        artist name, else ``None``.
    """
    track_count = entry.get("trackCount")
    artist = entry.get("artistName")
    if isinstance(track_count, int) and track_count > 0:
        return (
            f"{track_count:,} episodes · {artist}"
            if artist
            else f"{track_count:,} episodes"
        )
    return artist or None


async def _search_x_account(query: str) -> list[SourceSearchResult]:
    """Resolve an X handle/URL into a single addable ``x_account`` result.

    Delegates to the build-fresh resolver (`agents/ingestion/adapters/x_resolver`).
    With no live X lookup wired (open question #3), the result is a PENDING
    free-text follow (``is_pending=True``) — the DoD fallback, a valid result, not
    an error. An unparseable input returns ``[]`` (the user typed garbage).

    Args:
        query: The free-text X query (``@handle`` or profile URL).

    Returns:
        A single-element list with the resolved/pending result, or ``[]`` when the
        input cannot be parsed into a handle.
    """
    try:
        # Reason: live_lookup is None — no X API key is wired yet (open question
        # #3). The resolver returns a pending free-text follow, which is addable.
        resolution = await resolve_x_handle(query, live_lookup=None)
    except XHandleParseError as exc:
        logger.info(
            "source_search_x_unparseable",
            kind="x_account",
            error_message=exc.message,
            fix_suggestion=exc.fix_suggestion,
        )
        return []

    return [
        SourceSearchResult(
            source_name=resolution.display_name,
            external_id=resolution.external_id,
            content_source_type="x_account",
            thumbnail_url=resolution.profile_image_url,
            description=f"@{resolution.handle}",
            subscriber_count=None,
            is_pending=resolution.is_pending,
        )
    ]


@app.post("/api/sources/search")
async def post_source_search(request: SourceSearchRequest) -> SourceSearchResponse:
    """Live "add anyone" source search across the worker-searchable axes.

    Dispatches by ``kind``: YouTube channels (2-step Data API), podcasts (iTunes),
    or an X handle (build-fresh resolver → pending free-text follow). Every
    provider failure degrades to an empty, ``search_ok=False`` envelope and logs a
    typed error with ``fix_suggestion`` — never a 5xx (matches the worker's
    graceful-failure posture; Rule 12 keeps it honest via the ``search_ok`` flag).
    ``is_already_added`` is annotated client-side (the worker has no per-user
    follow context).

    Args:
        request: The :class:`SourceSearchRequest` body (``query`` + ``kind``).

    Returns:
        A :class:`SourceSearchResponse` with the addable results and ``search_ok``.
    """
    logger.info(
        "source_search_request_received",
        kind=request.kind,
        query_length=len(request.query),
    )
    try:
        if request.kind == "youtube_channel":
            results = await _search_youtube_channels(request.query)
        elif request.kind == "podcast":
            results = await _search_podcasts(request.query)
        else:  # x_account — the Literal type makes this exhaustive
            results = await _search_x_account(request.query)
    except _SourceSearchUnavailable:
        # Reason: the search could not RUN (missing key / upstream error) — already
        # logged at the failure site. Return the honest "unavailable" envelope so
        # the client shows "search unavailable", not a misleading empty "no matches".
        return SourceSearchResponse(results=[], search_ok=False)
    except Exception as exc:  # noqa: BLE001 — boundary: never 5xx; honest search_ok=False
        _log_source_search_error(
            error_code="source_search_unexpected",
            error_message=str(exc)[:200],
            kind=request.kind,
            fix_suggestion="Unexpected source-search error; check upstream provider reachability.",
        )
        return SourceSearchResponse(results=[], search_ok=False)

    logger.info("source_search_completed", kind=request.kind, result_count=len(results))
    return SourceSearchResponse(results=results, search_ok=True)
