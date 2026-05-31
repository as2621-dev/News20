"""Per-story grounding-corpus cache for the Q&A endpoint (Phase 2b SP2).

The per-story corpus is identical for every question about the same story, so we
cache the **assembled** :class:`~agents.qa.models.GroundingCorpus` in-process and
reuse it across requests — repeat questions about one story skip the Supabase
reads + corpus assembly entirely. The cached object IS the context block the
prompt embeds (``corpus.render_context_block()``), so this is the per-story
"prompt/context cache" the phase asks for, at the layer we control cheaply.

WHY NOT Gemini ``CachedContent`` (the documented hook, not faked)
-----------------------------------------------------------------
The Gemini SDK exposes explicit context caching
(``client.caches.create(...)`` → ``CachedContent``), but it has a **minimum
token floor** (a few thousand tokens) below which content cannot be cached. A
News20 per-story corpus is tiny by design (per-story, single-source, "<100s
read"; ``s1`` ≈ a few hundred tokens — well under the floor), so Gemini-side
context caching would be REJECTED for the common case and add a create/delete
lifecycle + TTL management for no benefit (Rule 2). We therefore cache the
assembled corpus in-process here, and leave the Gemini ``CachedContent`` hook
documented below for the escape-hatch case (a corpus that grows past the floor —
the same case that would also trip ``CorpusBudgetExceededError`` / retrieval).

# Reason (Gemini CachedContent hook): if a story's corpus ever exceeds Gemini's
# explicit-cache token floor, create a CachedContent for its context block once
# (client.caches.create(model=..., config=CreateCachedContentConfig(
#     contents=[context_block], ttl="3600s"))) and pass cached_content=<name> in
# the GenerateContentConfig of agents/qa/agent.py's call. Keyed by story_id with
# a TTL refresh. Not wired now because the corpus is below the floor (see above).

This in-process cache is process-local (each worker instance has its own) and
unbounded only by the number of distinct stories asked about in a process
lifetime — fine for the M2 demo's small story set; swap for an LRU / shared
cache if the worker fleet serves a large catalog (flagged for SP4/CSO).
"""

from __future__ import annotations

from typing import Any, Callable

from agents.qa.models import GroundingCorpus
from agents.shared.logger import get_logger

logger = get_logger("worker.corpus_cache")

# Reason: process-local cache of assembled corpora, keyed by story_id. The
# corpus is deterministic for a story (content table, no per-user scope), so a
# cached entry is always valid for the process lifetime in the M2 demo.
_CORPUS_CACHE: dict[str, GroundingCorpus] = {}


def get_or_load_corpus(
    story_id: str,
    supabase_client: Any,
    loader: Callable[[str, Any], GroundingCorpus],
) -> GroundingCorpus:
    """Return the cached corpus for ``story_id`` or load + cache it via ``loader``.

    On a cache HIT, ``loader`` (and thus every Supabase read) is skipped — the
    per-story context-cache win. On a MISS, ``loader(story_id, supabase_client)``
    is called and its result cached. Loader exceptions propagate (the endpoint
    catches them and returns the graceful refusal); a failed load is NOT cached.

    Args:
        story_id: The story whose corpus to fetch.
        supabase_client: The injected service-role Supabase client (passed
            through to ``loader`` on a miss; unused on a hit).
        loader: The corpus loader (``agents.qa.corpus.load_grounding_corpus``),
            injected so this stays testable without the network.

    Returns:
        The cached or freshly-loaded :class:`GroundingCorpus`.

    Example:
        >>> corpus = get_or_load_corpus("s1", client, load_grounding_corpus)  # doctest: +SKIP
        >>> corpus.story_id
        's1'
    """
    cached = _CORPUS_CACHE.get(story_id)
    if cached is not None:
        logger.info("corpus_cache_hit", story_id=story_id)
        return cached

    logger.info("corpus_cache_miss", story_id=story_id)
    corpus = loader(story_id, supabase_client)
    _CORPUS_CACHE[story_id] = corpus
    return corpus


def clear_corpus_cache() -> None:
    """Clear the in-process corpus cache (test hygiene / forced refresh)."""
    _CORPUS_CACHE.clear()
