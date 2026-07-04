"""Interview guardrail thresholds and the fixed root bubbles (spec §3).

The roots come from :data:`agents.pipeline.categories.TOPIC_CATEGORIES` — the single
source of truth for the 8 onboarding roots — so the interview can never drift from the
taxonomy the ranker/allocator use. The two source axes (youtube/x) are deliberately
excluded: they are followed-source surfaces, not interview roots.
"""

from __future__ import annotations

from agents.pipeline.categories import TOPIC_CATEGORIES

# Guardrails (spec §5). Hard caps are enforced in code, never by the model.
MAX_OPTION_BUBBLES: int = 6  # <= 6 generated bubbles (before skip / type-your-own)
MAX_FREE_TEXT_ONLY_TURNS: int = 2  # ask once more, then park (never loop on gibberish)

# WHO-drill compression (spec §5): exactly one open-ended WHO drill per selected sub-niche,
# but if the total would exceed this cap, the first (cap - 1) get their own drill and every
# remaining sub-niche folds into ONE combined drill — so the user never faces 40 questions.
MAX_WHO_DRILLS: int = 8

# The two always-present affordances (spec §3): skip and type-your-own.
SKIP_BUBBLE_LABEL: str = "Not really / skip"
TYPE_YOUR_OWN_BUBBLE_LABEL: str = "Something else — type it"

# The "Nothing specific →" affordance on every open-ended WHO drill (spec §2).
WHO_DRILL_NOTHING_LABEL: str = "Nothing specific →"

# Deterministic skip-fast-forward offer labels (spec §5). These are FIXED so the stateless
# replay can detect which control choice the user made from the echoed taps — engine-owned,
# never model-worded.
CATEGORY_SKIP_ACCEPT_LABEL: str = "Skip this topic for now"
CATEGORY_SKIP_DECLINE_LABEL: str = "Show me some options"
BUILD_FEED_ACCEPT_LABEL: str = "Just build my feed"
BUILD_FEED_DECLINE_LABEL: str = "Keep exploring"

# The fixed turn-1 question — deterministic, no LLM.
ROOT_QUESTION_TEXT: str = (
    "What kind of news do you want in your feed? Tap all that pull you in."
)

# Root slug -> display label (user-facing). Order follows TOPIC_CATEGORIES.
ROOT_LABEL_BY_SLUG: dict[str, str] = {
    "ai": "AI",
    "geopolitics": "Geopolitics",
    "business": "Business",
    "environment": "Environment",
    "politics": "Politics",
    "tech": "Tech",
    "sport": "Sport",
    "arts": "Arts",
}

# Reason: fail loud if the taxonomy adds/renames a root without updating the label map,
# rather than silently dropping a root bubble from onboarding.
_missing_labels = [slug for slug in TOPIC_CATEGORIES if slug not in ROOT_LABEL_BY_SLUG]
if _missing_labels:
    raise RuntimeError(
        f"ROOT_LABEL_BY_SLUG is missing labels for roots {_missing_labels}; "
        "update agents/interview/constants.py to match TOPIC_CATEGORIES."
    )

# Ordered (slug, label) root bubbles for turn 1.
ROOT_BUBBLES: tuple[tuple[str, str], ...] = tuple(
    (slug, ROOT_LABEL_BY_SLUG[slug]) for slug in TOPIC_CATEGORIES
)

# Reverse map: a turn-1 tapped label -> its root slug (case-insensitive).
ROOT_SLUG_BY_LABEL: dict[str, str] = {
    label.casefold(): slug for slug, label in ROOT_BUBBLES
}

# The valid root slugs, for terminal slug-anchoring checks.
VALID_ROOT_SLUGS: frozenset[str] = frozenset(TOPIC_CATEGORIES)
