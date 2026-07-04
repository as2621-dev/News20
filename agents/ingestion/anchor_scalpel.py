"""Per-anchor GDELT DOC 2.0 scalpel — additive niche enrichment (issue #16).

The BigQuery GKG adapter (``gdelt_bigquery.py``) is the batched, unthrottled
*workhorse* — one SQL matches every active micro-interest at once. But GDELT's
BigQuery crawl skews to high-volume mainstream outlets, so long-tail WHO anchors
(e.g. an individual cricketer) can go uncovered there. This module is the
**scalpel**: a *single sequential* GDELT DOC 2.0 loop that fires one exact-phrase
query per anchor term for interests the BigQuery batch missed, tagging each hit
to its interest node so the shipped niche-first fallback ladder surfaces it.

Non-negotiable pacing (reference/integrations.md, GDELT_API_specs — live-verified
2026-07-04): DOC is ~**1 request / 5 s / IP**, with a multi-minute penalty box on
abuse. So this loop is:

  • **Sequential — never parallel.** Anchors are awaited one at a time (no
    ``asyncio.gather``); the throttle + penalty-box backoff live in the injected
    :class:`GdeltDocAdapter` (its ``_throttled_get`` spaces calls >=5 s and backs
    off on a rate-limit signal). A penalty box surfaces as an ``AdapterFetchError``
    for that one anchor — we log it and resume the loop at the next anchor, never
    parallel-retrying.
  • **Gap-only.** Interests the BigQuery batch already covered are skipped, so the
    throttled DOC budget is spent only where the backbone came up empty.
  • **Per-anchor capped.** Each anchor contributes at most ``per_anchor_cap``
    candidates, bounding junk volume before the shared dedup/importance passes.

The function is additive and pure over its injected adapter: on a DOC outage it
returns whatever it gathered (possibly nothing) and NEVER raises — a BigQuery-only
night is a valid night; the backbone pool is untouched (Rule 12: the outage is
logged loudly with a ``fix_suggestion``, not swallowed silently).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.models import ActiveInterest, CandidateStory
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Iterable

logger = get_logger(__name__)

# Reason: the interview persists a micro-interest's >=2 WHO anchor terms JOINED
# with ", " into the leaf ``interest_search_query`` (see src/lib/interviewProfile.ts
# ``persistInterviewInterests``). We split back on that exact separator to recover
# the individual anchor phrases the scalpel queries one at a time.
_ANCHOR_SEPARATOR = ","

# Reason: per-anchor cap bounds junk volume BEFORE the shared clusterer/importance
# passes (an over-broad anchor like a common surname can match dozens of unrelated
# stories). Mirrors the DOC ``maxrecords`` sizing but tighter — the scalpel is a
# gap-filler, not a bulk source; the fallback ladder only needs a few real hits.
_DEFAULT_PER_ANCHOR_CAP = 25

# Reason: an anchor phrase is wrapped in double quotes for an EXACT-PHRASE DOC query
# (the spec: wrap exact names in quotes → ``"Vaibhav Suryavanshi"``). Any embedded
# double-quote is stripped first so a crafted anchor term cannot break out of the
# quoted phrase and inject raw DOC query operators (B7 injection hygiene).
_WHITESPACE_RUN = re.compile(r"\s+")


def split_anchor_terms(interest_search_query: str | None) -> list[str]:
    """Recover the distinct anchor phrases from a joined ``interest_search_query``.

    The interview joins a micro-interest's >=2 anchor terms with ``", "`` into one
    ``interest_search_query`` string; this splits them back apart, trims whitespace,
    drops empties, and de-duplicates preserving first-seen order.

    Args:
        interest_search_query: The leaf node's joined query (may be None/empty).

    Returns:
        The ordered, de-duplicated list of non-empty anchor phrases.

    Example:
        >>> split_anchor_terms("Vaibhav Suryavanshi, IPL auction, Vaibhav Suryavanshi")
        ['Vaibhav Suryavanshi', 'IPL auction']
        >>> split_anchor_terms(None)
        []
    """
    if not interest_search_query:
        return []
    seen: set[str] = set()
    anchors: list[str] = []
    for raw in interest_search_query.split(_ANCHOR_SEPARATOR):
        anchor = _WHITESPACE_RUN.sub(" ", raw).strip()
        if anchor and anchor.lower() not in seen:
            seen.add(anchor.lower())
            anchors.append(anchor)
    return anchors


def build_anchor_doc_query(anchor_term: str) -> str:
    """Wrap one anchor phrase in an exact-phrase, injection-safe DOC query.

    Strips any embedded double-quote (so the term cannot escape the quoted phrase
    and inject DOC operators — B7), collapses whitespace, and wraps the result in
    double quotes per the GDELT DOC 2.0 spec (exact names are quoted).

    Args:
        anchor_term: A single anchor phrase (e.g. ``Vaibhav Suryavanshi``).

    Returns:
        The quoted DOC query string, or ``""`` when the term is empty after
        sanitization (the caller skips empties).

    Example:
        >>> build_anchor_doc_query('Vaibhav Suryavanshi')
        '"Vaibhav Suryavanshi"'
        >>> build_anchor_doc_query('"drop table" OR x')
        '"drop table OR x"'
    """
    sanitized = _WHITESPACE_RUN.sub(" ", anchor_term.replace('"', " ")).strip()
    if not sanitized:
        return ""
    return f'"{sanitized}"'


async def run_anchor_scalpel(
    active_interests: Iterable[ActiveInterest],
    doc_adapter: BaseNewsAdapter,
    since_utc: datetime,
    *,
    per_anchor_cap: int = _DEFAULT_PER_ANCHOR_CAP,
    covered_interest_ids: set[str] | None = None,
) -> list[CandidateStory]:
    """Run the single sequential per-anchor DOC loop; return tagged candidates.

    For each active interest the BigQuery batch did NOT already cover, split its
    ``interest_search_query`` into anchor phrases and fire ONE exact-phrase DOC
    query per anchor — strictly sequentially (never parallel). Each anchor's hits
    are capped to ``per_anchor_cap`` and stamped with the interest's id + slug so
    the niche-first assembler surfaces them at the leaf.

    Resilience (Rule 12): a per-anchor ``AdapterFetchError`` (a penalty box, a bad
    query, exhausted backoff) is logged and the loop RESUMES at the next anchor —
    never a parallel retry. If EVERY attempted anchor errors (a DOC outage), the
    function logs loudly at error level and returns whatever it gathered — a
    BigQuery-only night; it never raises, so the backbone pool is unaffected.

    Args:
        active_interests: The active-interest set (each carries a joined query).
        doc_adapter: The shared, throttled GDELT DOC adapter (owns the >=5 s
            spacing + penalty-box backoff). Injected so tests mock the boundary.
        since_utc: Lower-bound article time (→ the adapter's ``timespan``).
        per_anchor_cap: Max candidates kept per anchor (bounds junk volume).
        covered_interest_ids: Interest ids the BigQuery batch already returned
            candidates for — skipped, so the DOC budget targets only the gaps.

    Returns:
        Interest-stamped candidates (one per surfaced article, per anchor).
    """
    covered = covered_interest_ids or set()
    cap = max(1, per_anchor_cap)
    collected: list[CandidateStory] = []
    anchors_attempted = 0
    anchors_failed = 0
    interests_scanned = 0

    for interest in active_interests:
        if interest.interest_id in covered:
            continue
        anchors = split_anchor_terms(interest.interest_search_query)
        if not anchors:
            continue
        interests_scanned += 1
        for anchor in anchors:
            doc_query = build_anchor_doc_query(anchor)
            if not doc_query:
                continue
            anchors_attempted += 1
            # Reason: awaited ONE AT A TIME — the sequential contract. The adapter
            # serializes + paces (>=5 s) and backs off on a penalty box internally;
            # we never wrap this in gather, so two anchors are never in flight at once.
            try:
                candidates = await doc_adapter.search(doc_query, since_utc)
            except AdapterFetchError as exc:
                anchors_failed += 1
                logger.warning(
                    "anchor_scalpel_anchor_failed",
                    interest_slug=interest.interest_slug,
                    anchor=anchor[:120],
                    error_message=str(exc)[:300],
                    fix_suggestion="DOC query failed (penalty box / transient / bad "
                    "query); resuming the sequential loop at the next anchor — never "
                    "parallel-retrying. This anchor contributes no candidates this run.",
                )
                continue
            capped = candidates[:cap]
            for candidate in capped:
                candidate.candidate_matched_interest_id = interest.interest_id
                candidate.candidate_matched_interest_slug = interest.interest_slug
            collected.extend(capped)

    # DOC outage: every anchor we tried errored → a BigQuery-only night. Log LOUDLY
    # (Rule 12) — the backbone pool is untouched; this is a degraded-but-valid run.
    if anchors_attempted > 0 and anchors_failed == anchors_attempted:
        logger.error(
            "anchor_scalpel_doc_outage",
            anchors_attempted=anchors_attempted,
            interests_scanned=interests_scanned,
            fix_suggestion="GDELT DOC failed for EVERY anchor this run (likely a "
            "penalty box or endpoint outage) — the scalpel added zero niche "
            "candidates. The BigQuery backbone pool is unaffected; a fresh IP or a "
            "later retry recovers. Check network reachability to api.gdeltproject.org.",
        )

    logger.info(
        "anchor_scalpel_completed",
        interests_scanned=interests_scanned,
        anchors_attempted=anchors_attempted,
        anchors_failed=anchors_failed,
        candidates_collected=len(collected),
    )
    return collected
