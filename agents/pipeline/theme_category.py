"""Pure ``V2Themes`` → ``FeedCategory`` whitelist (Phase FSR-M2, Sub-phase 1).

A story's news category should come from what the story is *about* — its GDELT GKG
``V2Themes`` codes — NOT from which keyword-matched interest happened to fetch it (the
M2 bug: a retail-takeover story labelled GEOPOLITICS because it matched a geopolitics
search term). This module is the pure, deterministic, fail-loud map from a story's
theme codes to one of the **8 topic categories** in :mod:`agents.pipeline.categories`
(``ai, geopolitics, business, environment, politics, tech, sport, arts``).

It is intentionally tiny and **pure** — no DB, no clock, no network — so the ingestion
wiring (SP3) imports :func:`category_for_themes` rather than re-deriving the mapping.

Matching convention (pinned): **exact theme-code match only.** GDELT GKG theme codes
are stable UPPERCASE underscore strings (``ECON_STOCKMARKET``, ``ENV_CLIMATECHANGE``,
``WB_2670_JOBS`` …); an exact-set membership lookup is the simplest, most testable
convention and is what the SP1 DoD requires. Prefix matching is deliberately NOT done
(Rule 2 — no speculative generality): it would need its own ambiguity rules + tests
and the offline DoD only asks for representative coverage. Broadening the whitelist is
the stated LIVE-E2E tuning follow-up (phase Open Question 2), not a blocker here.

Coverage here is **representative**, not exhaustive: ~2-4 plausible real GKG codes per
category, enough to prove the map + tiebreak + fallback. Exhaustive taxonomy curation
is the LIVE-E2E follow-up.
"""

from __future__ import annotations

from agents.pipeline.categories import (
    DEFAULT_CATEGORY,
    TOPIC_CATEGORIES,
    FeedCategory,
)
from agents.shared.logger import get_logger

logger = get_logger(__name__)

# Reason: representative GDELT GKG ``V2Themes`` codes → one of the 8 topic categories.
# Exact-code keys (see module docstring for why exact, not prefix). Each cluster is
# documented with a ``# Reason:`` for the category it lands on. Every VALUE must be a
# member of TOPIC_CATEGORIES (never a source axis youtube/x, never a typo) — the SP1
# test enforces this drift guard.
THEME_CATEGORY_WHITELIST: dict[str, FeedCategory] = {
    # Reason: AI / machine-learning themes — GDELT tags these under its SCI/TECH
    # space; AI is its own first-class root (NOT folded into tech).
    "WB_2479_TERRORISM_AND_COUNTERTERRORISM_AI": "ai",
    "TECH_ARTIFICIAL_INTELLIGENCE": "ai",
    "SCI_ARTIFICIAL_INTELLIGENCE": "ai",
    "WB_678_DIGITAL_GOVERNMENT_ARTIFICIAL_INTELLIGENCE": "ai",
    # Reason: geopolitics — cross-border conflict / diplomacy / sanctions themes.
    "MILITARY": "geopolitics",
    "ARMEDCONFLICT": "geopolitics",
    "WB_2467_TERRORISM": "geopolitics",
    "EPU_POLICY_FOREIGN_RELATIONS": "geopolitics",
    # Reason: business / markets / economy themes.
    "ECON_STOCKMARKET": "business",
    "WB_2670_JOBS": "business",
    "EPU_ECONOMY": "business",
    "ECON_BANKRUPTCY": "business",
    # Reason: environment / climate themes — environment is a first-class root.
    "ENV_CLIMATECHANGE": "environment",
    "ENV_NATURALDISASTER": "environment",
    "WB_567_AIR_POLLUTION": "environment",
    "ENV_CARBONCAPTURE": "environment",
    # Reason: domestic politics / elections / governance themes (NOT geopolitics).
    "ELECTION": "politics",
    "EPU_POLICY_GOVERNMENT": "politics",
    "GENERAL_GOVERNMENT": "politics",
    "DEMOCRACY": "politics",
    # Reason: tech (ex-AI) — software, devices, cyber, science-and-engineering.
    "TECH_CYBERSECURITY": "tech",
    "SCI_SPACE": "tech",
    "WB_1467_SCIENCE_AND_TECHNOLOGY": "tech",
    "TECH_SEMICONDUCTORS": "tech",
    # Reason: sport themes.
    "SPORT": "sport",
    "WB_1953_SPORTS": "sport",
    "SOC_SPORTS": "sport",
    # Reason: arts / culture / entertainment — also the long-tail catch-all root.
    "ARTS": "arts",
    "ENTERTAINMENT": "arts",
    "SOC_POINTSOFINTEREST_MUSEUMS": "arts",
    "WB_1803_CULTURE": "arts",
}

# Reason: the deterministic tiebreak priority order when two categories tie on
# whitelist hit-count. Pinned ONCE (the SP1 test fails if this order changes). The
# order encodes editorial salience for News20's feed: hard-news / fast-moving roots
# rank above evergreen/long-tail ones, so a mixed story leans to the more
# newsworthy bucket. ``arts`` is last (it is also DEFAULT_CATEGORY, the catch-all).
_TIEBREAK_PRIORITY: tuple[FeedCategory, ...] = (
    "geopolitics",
    "politics",
    "business",
    "ai",
    "tech",
    "environment",
    "sport",
    "arts",
)


def category_for_themes(themes: list[str]) -> FeedCategory:
    """Resolve a story's GDELT ``V2Themes`` codes to one ``FeedCategory`` (fail-loud).

    Pure and deterministic. Each theme is looked up in
    :data:`THEME_CATEGORY_WHITELIST` (exact-code match). The winning category is
    chosen by this **pinned tiebreak rule**:

      1. Highest whitelist hit-count wins (the category the most of the story's
         themes map to).
      2. Ties broken by the fixed :data:`_TIEBREAK_PRIORITY` order (editorial
         salience; lower index wins).

    This rule is intentionally rigid so the categorization is reproducible run-to-run
    (Rule 9: a test pins the winner of a crafted mixed list and fails if the rule
    changes).

    A theme list with **no whitelisted theme** (or an empty list) returns
    :data:`DEFAULT_CATEGORY` and emits a structured ``logger.warning`` carrying a
    ``fix_suggestion`` to extend the whitelist — it never raises and never silently
    drops a story (Rule 12: fail loud, but resiliently — one bad/unknown story falls
    back, it does not abort the batch).

    Args:
        themes: The story's GDELT GKG ``V2Themes`` codes (offset-stripped, e.g.
            ``["ECON_STOCKMARKET", "WB_2670_JOBS"]``). May be empty.

    Returns:
        Exactly one of the 8 topic ``FeedCategory`` roots.

    Example:
        >>> category_for_themes(["ECON_STOCKMARKET"])
        'business'
        >>> category_for_themes(["ENV_CLIMATECHANGE", "ECON_STOCKMARKET", "WB_2670_JOBS"])
        'business'
        >>> category_for_themes([])
        'arts'
    """
    # Reason: count hits per category in one pass (deterministic; no model, Rule 5).
    hit_counts: dict[FeedCategory, int] = {}
    for theme in themes:
        category = THEME_CATEGORY_WHITELIST.get(theme)
        if category is not None:
            hit_counts[category] = hit_counts.get(category, 0) + 1

    if not hit_counts:
        # Reason: fail loud but resilient — no recognized theme means we cannot
        # derive the category, so fall back to the long-tail default AND surface it
        # so the whitelist can be extended (the no-whitelisted-theme / empty case).
        logger.warning(
            "theme_category_no_whitelisted_theme",
            theme_count=len(themes),
            themes=themes[:20],
            fallback_category=DEFAULT_CATEGORY,
            fix_suggestion=(
                "No V2Themes code matched THEME_CATEGORY_WHITELIST; add the "
                "representative code(s) for this story's themes to the whitelist "
                "in agents/pipeline/theme_category.py"
            ),
        )
        return DEFAULT_CATEGORY

    max_hits = max(hit_counts.values())
    # Reason: among the categories tied on max hits, pick the first by the pinned
    # priority order — deterministic, no lexical/dict-order dependence.
    for category in _TIEBREAK_PRIORITY:
        if hit_counts.get(category) == max_hits:
            return category

    # Unreachable: every whitelist value is in _TIEBREAK_PRIORITY (== TOPIC_CATEGORIES).
    # Reason: defensive fail-loud rather than returning None on an impossible state.
    raise AssertionError(
        "tiebreak fell through: a whitelist category is missing from "
        "_TIEBREAK_PRIORITY — keep it in sync with TOPIC_CATEGORIES"
    )


# Reason: module-load self-check that _TIEBREAK_PRIORITY covers exactly the 8 topic
# roots, so the tiebreak loop can never fall through for a valid whitelist value.
# A drifted categories.py would trip this at import (fail loud, Rule 12).
assert set(_TIEBREAK_PRIORITY) == set(TOPIC_CATEGORIES), (
    "_TIEBREAK_PRIORITY must cover exactly TOPIC_CATEGORIES"
)
