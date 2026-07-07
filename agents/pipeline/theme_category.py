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
and the offline DoD only asks for representative coverage. Whitelist broadening is an
ongoing, data-driven activity (see below); re-tally with scripts/theme_miss_counts.py.

Coverage here started **representative** (~2-4 plausible real GKG codes per
category). Issue #35 (2026-07-07) expanded it DATA-DRIVEN: a real production-shaped
batch (live GdeltBigQueryAdapter over the prod active-interest set; 2,368 canonical
stories, 884 with no whitelisted theme) was tallied and only the OBSERVED top-miss
codes with a crisp single-category meaning were added (counts inline below).
Generic role/sentiment/crisis codes (``TAX_FNCACT_*``, ``CRISISLEX_*``, ``LEADER``,
``AFFECT``, ``KILL`` …) are deliberately NOT mapped — they are not category-
indicative, and an unmatched story now falls back to its fetching interest's root
(never an arts default), so a missing entry is safe.
"""

from __future__ import annotations

from agents.pipeline.categories import (
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
    # ── Issue #35 data-driven expansion (2026-07-07) ────────────────────────────
    # Observed top-miss codes from a real batch (story-level counts on the 884
    # no-whitelisted-theme stories); each mapped only where the code has ONE crisp
    # category meaning. See module docstring for the methodology + exclusions.
    # Reason: environment — natural-resource / disaster / ecosystem codes.
    "UNGP_FORESTS_RIVERS_OCEANS": "environment",  # 303
    "MANMADE_DISASTER_IMPLIED": "environment",  # 132
    "WB_566_ENVIRONMENT_AND_NATURAL_RESOURCES": "environment",  # 67
    "WB_590_ECOSYSTEMS": "environment",  # 58
    "WB_137_WATER": "environment",  # 44
    # Reason: business — economy / prices / trade / industry / energy-sector codes.
    "EPU_ECONOMY_HISTORIC": "business",  # 205
    "TAX_ECON_PRICE": "business",  # 173
    "WB_507_ENERGY_AND_EXTRACTIVES": "business",  # 93
    "WB_698_TRADE": "business",  # 82
    "WB_1921_PRIVATE_SECTOR_DEVELOPMENT": "business",  # 72
    "WB_346_COMPETITIVE_INDUSTRIES": "business",  # 50
    "WB_818_INDUSTRY_POLICY_AND_REAL_SECTORS": "business",  # 47
    # Reason: tech — ICT / science / innovation codes, plus the health cluster
    # (health has no root of its own; SLUG_TO_CATEGORY pins health → tech).
    "WB_133_INFORMATION_AND_COMMUNICATION_TECHNOLOGIES": "tech",  # 159
    "WB_621_HEALTH_NUTRITION_AND_POPULATION": "tech",  # 132
    "GENERAL_HEALTH": "tech",  # 112
    "MEDICAL": "tech",  # 102
    "SCIENCE": "tech",  # 88
    "WB_658_ENTERPRISE_APPLICATIONS": "tech",  # 49
    "WB_652_ICT_APPLICATIONS": "tech",  # 49
    "SOC_INNOVATION": "tech",  # 44
    # Reason: politics — domestic policy / governance / justice codes.
    "USPEC_POLICY1": "politics",  # 153
    "WB_678_DIGITAL_GOVERNMENT": "politics",  # 135
    "WB_696_PUBLIC_SECTOR_MANAGEMENT": "politics",  # 124
    "USPEC_POLITICS_GENERAL1": "politics",  # 94
    "WB_831_GOVERNANCE": "politics",  # 55
    "WB_840_JUSTICE": "politics",  # 48
    "EPU_POLICY_POLICY": "politics",  # 47
    # Reason: geopolitics — cross-border conflict/fragility codes.
    "WB_2432_FRAGILITY_CONFLICT_AND_VIOLENCE": "geopolitics",  # 62
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


def category_for_themes(themes: list[str]) -> FeedCategory | None:
    """Resolve a story's GDELT ``V2Themes`` codes to one ``FeedCategory``, or ``None``.

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

    A theme list with **no whitelisted theme** returns ``None`` and emits a
    structured ``logger.info`` naming the top unmatched codes (the input to the
    data-driven whitelist expansion — scripts/theme_miss_counts.py aggregates the
    same signal). An **empty** theme list also returns ``None`` but logs only a
    debug event — there is nothing to whitelist. ``None`` means "the themes carry
    no category signal" — the caller must let the FETCHING interest's root own
    categorization (issue #35: a theme-derived category may only win when a
    whitelisted theme actually matched; the old behavior returned the arts default
    here, which the caller stamped at depth 0 and mis-bucketed every unmatched
    story into arts). Neither case is an error: post-#35 the fetching-interest
    fallback is the DESIGNED path, so this is routine signal, not a warning.

    Args:
        themes: The story's GDELT GKG ``V2Themes`` codes (offset-stripped, e.g.
            ``["ECON_STOCKMARKET", "WB_2670_JOBS"]``). May be empty.

    Returns:
        One of the 8 topic ``FeedCategory`` roots when at least one theme matched
        the whitelist; ``None`` when none did (no category signal).

    Example:
        >>> category_for_themes(["ECON_STOCKMARKET"])
        'business'
        >>> category_for_themes(["ENV_CLIMATECHANGE", "ECON_STOCKMARKET", "WB_2670_JOBS"])
        'business'
        >>> category_for_themes([]) is None
        True
    """
    # Reason: count hits per category in one pass (deterministic; no model, Rule 5).
    hit_counts: dict[FeedCategory, int] = {}
    for theme in themes:
        category = THEME_CATEGORY_WHITELIST.get(theme)
        if category is not None:
            hit_counts[category] = hit_counts.get(category, 0) + 1

    if not hit_counts:
        # Reason: no recognized theme means the themes carry NO category signal, so
        # return None (the fetching interest's root then owns categorization —
        # issue #35, the DESIGNED fallback, hence info/debug not warning). The
        # non-empty case still surfaces the unmatched codes as the raw input to
        # data-driven whitelist expansion (scripts/theme_miss_counts.py).
        if themes:
            logger.info(
                "theme_category_no_whitelisted_theme",
                theme_count=len(themes),
                themes=themes[:20],
                fix_suggestion=(
                    "No V2Themes code matched THEME_CATEGORY_WHITELIST — the "
                    "fetching interest's root categorizes this story (by design). "
                    "If one of these codes SHOULD drive categorization, add it to "
                    "the whitelist in agents/pipeline/theme_category.py backed by "
                    "scripts/theme_miss_counts.py counts"
                ),
            )
        else:
            # Nothing to whitelist for a zero-theme story — routine, debug only.
            logger.debug("theme_category_no_themes")
        return None

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
