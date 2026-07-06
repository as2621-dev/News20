"""The X theme-of-the-day ladder (FSR slice #24) — pure slot planning.

The honesty core for X source slots, the mirror of the niche fallback ladder
(:func:`agents.pipeline.feed_assembly._fill_niche_section`, slice #7) applied to a
user's ``x`` allocation. Given the theme reels produced today from the user's
FOLLOWED clusters (from the shared once-daily sweep, ``x_cluster_sweeps`` /
slice #23) and how many ``x`` slots the user allocated, this walks the ladder ONE
rung at a time and stamps which rung filled each slot so the UI can be honest:

    theme-of-the-day → second theme → roundup-of-takes → news floor

Rules this module owns deterministically (CLAUDE.md Rule 5 — code, never a model):
  • **Dedup** — two clusters converging on the SAME theme for one user yield ONE
    reel (deduped by a normalized theme key; the converging clusters' handles +
    tweet urls MERGE into one attribution so credit is complete).
  • **Rung order** — the top theme fills the first X slot (``theme``), the next
    distinct theme the second (``second_theme``); any further distinct themes are
    rolled into a SINGLE ``roundup`` reel for the third X slot. Slots beyond that —
    and every slot on a quiet-cluster day with too few themes — are left UNFILLED so
    the caller rolls them to the news floor (never padded, never a faked theme).
  • **Attribution** — every placed rung carries the theme summary + the DISTINCT
    supporting handles + the supporting tweet urls it was drawn from.

Pure over its inputs (no DB, no clock, no network) — the theme reels are PRODUCED
upstream (screenshot + audio, exactly as followed-source reels are produced and
threaded in as ``source_stories``); this module only PLANS which produced reel fills
which X slot at which rung.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from agents.shared.logger import get_logger

logger = get_logger("pipeline.x_theme_ladder")

# Reason: the ladder rungs, written verbatim to daily_feeds.feed_x_theme_rung
# (migration 0032). The news floor is NOT a rung value — an X slot that falls to
# news carries a NULL rung (honest: it is real news, not a themed reel).
RUNG_THEME = "theme"
RUNG_SECOND_THEME = "second_theme"
RUNG_ROUNDUP = "roundup"

# Reason: the ordered theme rungs the ladder can place before the roundup. The first
# two distinct themes take the first two X slots; everything after rolls into ONE
# roundup reel. Kept as a tuple so the rung sequence is a single source of truth.
_THEME_RUNGS = (RUNG_THEME, RUNG_SECOND_THEME)

# Reason: collapse a theme summary to a dedup key — lowercase, strip punctuation,
# collapse whitespace — so two clusters phrasing the SAME theme slightly differently
# still converge to one reel. Deliberately simple (not fuzzy/semantic): a false
# non-merge just yields two honest theme slots, never a wrong merge.
_NON_KEY_CHARS = re.compile(r"[^a-z0-9\s]")
_WHITESPACE = re.compile(r"\s+")


class XThemeAttribution(BaseModel):
    """What an X theme reel credits — the handles + tweets it was drawn from.

    Persisted to ``daily_feeds.feed_x_theme_attribution`` (jsonb, migration 0032) and
    surfaced by the reel's honest credit line (slice #24 acceptance: "attributed
    handles"). ``supporting_handles`` is always the DISTINCT set (merged across any
    converging clusters).

    Deliberately SEPARATE from :class:`~agents.ingestion.cluster_sweep.ClusterTheme`
    (which carries the same three fields) — this is the FEED-LAYER persistence twin (the
    jsonb contract + the TS ``XThemeAttribution`` twin in ``src/types/feed.ts``), so it
    keeps the pipeline free of an ingestion-model dependency and, unlike ``ClusterTheme``
    (which enforces the >= 2-handle sweep gate), tolerates a merged/roundup attribution
    whose per-field lists were already validated upstream. Kept a plain twin, not a shared
    base, so each layer's invariants stay independent (Rule 7).

    Attributes:
        theme_summary: The one-line theme the reel is about.
        supporting_handles: The distinct handles whose posts back the theme.
        supporting_tweet_urls: The real tweet urls the theme was drawn from.
    """

    theme_summary: str = Field(..., description="One-line theme summary")
    supporting_handles: list[str] = Field(
        default_factory=list, description="Distinct backing handles"
    )
    supporting_tweet_urls: list[str] = Field(
        default_factory=list, description="Real backing tweet urls"
    )


class XThemeReelCandidate(BaseModel):
    """A theme reel PRODUCED today for one of the user's followed clusters.

    One candidate == one produced reel story (its ``reel_story_id`` is the persisted
    ``stories.story_id`` the daily_feeds slot will point at) plus the theme's
    attribution and a rank signal. Built upstream from ``x_cluster_sweeps.themes``
    (slice #23) + the tweet-screenshot reel image; this module never produces reels,
    it only orders + dedups + rungs them.

    Attributes:
        reel_story_id: The produced reel story id filling the slot.
        cluster_id: The followed cluster this theme came from (logging / provenance).
        attribution: The theme summary + supporting handles + tweet urls.
        reel_rank: The reel's rank signal (higher = louder theme). Ties break by the
            normalized theme key so ordering is deterministic (Rule 9).
    """

    reel_story_id: str = Field(..., description="Produced reel story id")
    cluster_id: str = Field(..., description="Followed cluster the theme came from")
    attribution: XThemeAttribution = Field(..., description="Theme attribution")
    reel_rank: float = Field(
        default=1.0, ge=0.0, description="Rank signal (higher = louder theme)"
    )


class XThemeLadderSlot(BaseModel):
    """One planned X slot fill: which produced reel, at which rung, crediting what.

    Maps to one ``daily_feeds`` X-theme row. ``rung`` is one of :data:`RUNG_THEME` /
    :data:`RUNG_SECOND_THEME` / :data:`RUNG_ROUNDUP` (never a news value — a news-floor
    X slot is not produced here at all; the caller rolls it to news with a NULL rung).

    Attributes:
        reel_story_id: The produced reel story id filling this X slot.
        rung: The ladder rung that filled it (theme / second_theme / roundup).
        attribution: The credited theme summary + handles + tweet urls.
        reel_score: The score written to ``daily_feeds.feed_score`` for the slot.
    """

    reel_story_id: str = Field(..., description="Produced reel story id")
    rung: str = Field(..., description="theme / second_theme / roundup")
    attribution: XThemeAttribution = Field(..., description="Credited attribution")
    reel_score: float = Field(default=1.0, ge=0.0, description="Slot score")


def _theme_key(theme_summary: str) -> str:
    """Normalize a theme summary into a dedup key (lowercase, de-punctuated, collapsed).

    Args:
        theme_summary: The raw one-line theme summary.

    Returns:
        A stable key; two summaries that normalize equal are the SAME theme.
    """
    lowered = _NON_KEY_CHARS.sub(" ", theme_summary.strip().lower())
    return _WHITESPACE.sub(" ", lowered).strip()


def _merge_distinct(primary: list[str], extra: list[str]) -> list[str]:
    """Merge ``extra`` into ``primary`` preserving order, case-insensitively distinct.

    Args:
        primary: The existing values (order preserved, kept first).
        extra: Values to fold in if not already present.

    Returns:
        The distinct union, primary-first, first-seen order.
    """
    seen = {value.strip().lower() for value in primary}
    merged = list(primary)
    for value in extra:
        key = value.strip().lower()
        if key and key not in seen:
            seen.add(key)
            merged.append(value)
    return merged


def dedup_theme_candidates(
    candidates: list[XThemeReelCandidate],
) -> list[XThemeReelCandidate]:
    """Collapse candidates that converge on ONE theme into a single reel, best-first.

    Two clusters reacting to the same theme for one user must yield ONE reel (slice #24
    dedup acceptance). Candidates are grouped by :func:`_theme_key`; within a group the
    highest-ranked candidate is kept as the reel (its ``reel_story_id`` wins) and the
    others' handles + tweet urls are MERGED into its attribution so credit stays
    complete. The returned list is ordered by rank desc, then theme key (deterministic).

    Args:
        candidates: Today's produced theme reel candidates (may contain converging themes).

    Returns:
        One candidate per distinct theme, ordered louder-first.
    """
    by_key: dict[str, XThemeReelCandidate] = {}
    for candidate in candidates:
        key = _theme_key(candidate.attribution.theme_summary)
        if not key:
            continue
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = candidate.model_copy(deep=True)
            continue
        # Keep the louder candidate as the reel; merge the quieter one's credit in.
        winner, loser = (
            (existing, candidate)
            if existing.reel_rank >= candidate.reel_rank
            else (candidate.model_copy(deep=True), existing)
        )
        winner.attribution.supporting_handles = _merge_distinct(
            winner.attribution.supporting_handles, loser.attribution.supporting_handles
        )
        winner.attribution.supporting_tweet_urls = _merge_distinct(
            winner.attribution.supporting_tweet_urls,
            loser.attribution.supporting_tweet_urls,
        )
        by_key[key] = winner

    deduped = sorted(
        by_key.values(),
        key=lambda c: (-c.reel_rank, _theme_key(c.attribution.theme_summary)),
    )
    if len(deduped) < len(candidates):
        logger.info(
            "x_theme_candidates_deduped",
            candidates_in=len(candidates),
            themes_out=len(deduped),
        )
    return deduped


def _roundup_attribution(
    roundup_candidates: list[XThemeReelCandidate],
) -> XThemeAttribution:
    """Fold several remaining themes into ONE roundup reel's attribution.

    The roundup-of-takes rung is a single reel summarizing the day's remaining themes
    (source-reels-spec §3). Its credit is the union of all folded themes' handles +
    tweet urls; the summary lists the folded theme summaries.

    Args:
        roundup_candidates: The remaining deduped candidates rolled into the roundup.

    Returns:
        The merged attribution for the roundup reel.
    """
    handles: list[str] = []
    tweet_urls: list[str] = []
    summaries: list[str] = []
    for candidate in roundup_candidates:
        summaries.append(candidate.attribution.theme_summary)
        handles = _merge_distinct(handles, candidate.attribution.supporting_handles)
        tweet_urls = _merge_distinct(
            tweet_urls, candidate.attribution.supporting_tweet_urls
        )
    return XThemeAttribution(
        theme_summary=" • ".join(summaries),
        supporting_handles=handles,
        supporting_tweet_urls=tweet_urls,
    )


def build_x_theme_ladder(
    candidates: list[XThemeReelCandidate],
    x_slot_budget: int,
    used_story_ids: set[str],
    excluded_story_ids: set[str],
) -> list[XThemeLadderSlot]:
    """Plan a user's X slots by walking the theme ladder, stamping each rung.

    Fills up to ``x_slot_budget`` X slots from ``candidates`` in ladder order:
    theme-of-the-day → second theme → roundup-of-takes. Whatever the ladder cannot fill
    honestly (too few themes, or budget beyond the roundup) is left for the caller to
    roll to the news floor — never padded, never a faked theme (PRD stories #30/#33).

    Args:
        candidates: Today's produced theme reel candidates (pre-dedup).
        x_slot_budget: How many ``x`` slots the user allocated.
        used_story_ids: Story ids already placed in this feed (mutated; dedup across
            the whole feed — a theme reel is never double-placed).
        excluded_story_ids: Prior-feed story ids to never repeat (§3.8).

    Returns:
        The planned X slots in ladder order (0..≤3). EMPTY on a quiet-cluster day.
    """
    if x_slot_budget <= 0:
        return []

    deduped = dedup_theme_candidates(candidates)
    # Drop any reel already placed elsewhere in the feed or shown in a prior feed.
    available = [
        candidate
        for candidate in deduped
        if candidate.reel_story_id not in used_story_ids
        and candidate.reel_story_id not in excluded_story_ids
    ]

    slots: list[XThemeLadderSlot] = []

    # ── Rungs 1–2: the top two distinct themes (theme-of-the-day, second theme) ──
    for rung, candidate in zip(_THEME_RUNGS, available):
        if len(slots) >= x_slot_budget:
            break
        slots.append(
            XThemeLadderSlot(
                reel_story_id=candidate.reel_story_id,
                rung=rung,
                attribution=candidate.attribution,
                reel_score=candidate.reel_rank,
            )
        )
        used_story_ids.add(candidate.reel_story_id)

    # ── Rung 3: roll every remaining distinct theme into ONE roundup reel ──
    remaining = available[len(_THEME_RUNGS) :]
    if remaining and len(slots) < x_slot_budget:
        roundup = remaining[0]
        slots.append(
            XThemeLadderSlot(
                reel_story_id=roundup.reel_story_id,
                rung=RUNG_ROUNDUP,
                attribution=_roundup_attribution(remaining),
                reel_score=roundup.reel_rank,
            )
        )
        used_story_ids.add(roundup.reel_story_id)

    logger.info(
        "x_theme_ladder_planned",
        x_slot_budget=x_slot_budget,
        candidates_in=len(candidates),
        distinct_themes=len(deduped),
        theme_slots=sum(1 for s in slots if s.rung in _THEME_RUNGS),
        roundup_slots=sum(1 for s in slots if s.rung == RUNG_ROUNDUP),
        news_floor_slots=max(x_slot_budget - len(slots), 0),
    )
    return slots
