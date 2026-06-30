"""Code-side long-vs-short summary-mode selector (feed-source revamp, M7).

A followed-source story is either **long-form** (a ``youtube.com`` video/podcast)
or **short-form** (an ``x.com`` tweet/short clip); a news (non-source) story is
neither. The summary *shape* the pipeline asks the model for differs by mode —
key-points for long-form, a tight summary for short-form, today's neutral
guidance for news.

This selection is a **deterministic transform** on data already in hand
(``CanonicalStory.canonical_primary_outlet_domain``), so it is decided in CODE,
not by the model (CLAUDE.md Rule 5). The domain set is single-sourced from
``agents/ingestion/dedup.is_source_origin_domain`` /
``SOURCE_ORIGIN_DOMAINS`` (Rule 7 — one source of truth for "what counts as a
followed-source domain"); this module only maps that signal to a mode.

No new ingestion, schema, or pipeline stage (PRD Decision #10) — this is a pure
selector consumed by the existing scripting + detail-enrichment stages.

Example:
    >>> from agents.pipeline.summary_mode import summary_mode_for
    >>> # a youtube.com story → "long"; an x.com story → "short"; else "news".
"""

from __future__ import annotations

from typing import Literal

from agents.ingestion.dedup import is_source_origin_domain
from agents.ingestion.models import CanonicalStory

SummaryMode = Literal["long", "short", "news"]

# Reason: the two source-origin domains carry different content lengths. Long-form
# (youtube.com) wants a key-points summary that draws the substance out of a
# 90-minute piece; short-form (x.com) wants a tight summary that does not pad a
# tweet into filler. Kept as named constants so the selector reads declaratively
# and the mapping is the single place "youtube → long / x → short" is decided.
_LONG_FORM_DOMAIN = "youtube.com"
_SHORT_FORM_DOMAIN = "x.com"


def summary_mode_for(story: CanonicalStory) -> SummaryMode:
    """Return the summary mode for a story from its outlet domain (Rule 5).

    A followed-source story's ``canonical_primary_outlet_domain`` is the
    deterministic long/short signal (set by the youtube.py / x_account.py
    adapters). News (non-source) stories are neither and keep today's prompt
    behavior.

    Args:
        story: The canonical story whose primary outlet domain decides the mode.

    Returns:
        ``"long"`` for a ``youtube.com`` source item, ``"short"`` for an
        ``x.com`` source item, ``"news"`` for any other (topic-news) outlet.

    Example:
        >>> # summary_mode_for(youtube_story) == "long"
        >>> # summary_mode_for(x_story) == "short"
        >>> # summary_mode_for(news_story) == "news"
    """
    domain = (story.canonical_primary_outlet_domain or "").strip().lower()
    # Reason: gate on the single-sourced source-origin set first, so a future
    # change to SOURCE_ORIGIN_DOMAINS cannot silently make this disagree with the
    # rest of the pipeline about what is a followed-source item (Rule 7).
    if not is_source_origin_domain(domain):
        return "news"
    if domain == _LONG_FORM_DOMAIN:
        return "long"
    if domain == _SHORT_FORM_DOMAIN:
        return "short"
    # Reason: defensive — a source-origin domain with no explicit long/short rule
    # falls back to news shaping rather than guessing (fail safe, not loud-crash:
    # an unrecognised source domain still produces a valid digest).
    return "news"
