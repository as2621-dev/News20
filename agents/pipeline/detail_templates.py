"""Per-category Detail-page panel templates (the backend source of truth).

The story Detail page (swipe-right from the reel, ``ArticleLayer``) renders an
ordered triple of panels. Which three depends on the story's **detail category** —
one of eight buckets (phase-SP1 removed ``breaking``). This module is the SINGLE
backend source of truth for that
mapping; ``src/lib/detailTemplates.ts`` is its byte-for-byte frontend twin (Rule 7:
the two must never drift — a parity test guards it).

Why a template instead of three fixed tabs: the old design showed Timeline /
one analytic / Coverage for every story, but Coverage is meaningless for Culture
(not partisan) and Market Impact is meaningless for Sport. Each category now
declares exactly the panels that make sense for it.

A template is a list of up to three :class:`PanelSpec`. A panel is one of:
  - ``timeline``  — the "HOW IT DEVELOPED" events (``story_timeline``)
  - ``coverage``  — the trust/coverage strip (``story_trust``), framed by ``coverage_mode``
  - ``analytic``  — one LLM-drafted analytic panel (``story_analytics`` row), of a given kind

The ``analytic`` panels of a template (in order) are exactly the panels the
enrichment LLM must produce, each persisted as a ``story_analytics`` row at its
``analytic_slot_index`` (its position among the analytic panels, 0-based).

Locked owner table (2026-06-16; ``breaking`` removed in phase-SP1):

    | category | slot 1            | slot 2                  | slot 3                       |
    |----------|-------------------|-------------------------|------------------------------|
    | world    | timeline          | stakes                  | coverage (partisan)          |
    | markets  | timeline          | market_impact           | by_the_numbers               |
    | tech     | timeline          | why_it_matters          | the_concept                  |
    | sport    | timeline          | stat_line               | recent_form                  |
    | culture  | timeline          | subject_profile         | why_it_matters               |
    | youtube  | source_context    | key_points              | implications                 |
    | podcasts | source_context    | key_points              | implications                 |
    | x        | source_context    | key_points              | implications                 |

Source categories (youtube/podcasts/x) carry NO timeline and NO coverage — their
stories are single-creator content, not multi-outlet news. Their enrichment is
deferred to phase-5d (source ingestion); the templates are defined now so they
light up the moment source ``stories`` rows exist.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from agents.pipeline.models import AnalyticKind, CoverageMode

# The Detail-page TEMPLATE buckets a story can fall into (phase-SP1 removed
# ``breaking``). These are panel-LAYOUT keys, NOT the SP3 picker taxonomy: each
# distinct panel layout keeps one canonical key. The SP3 taxonomy roots
# (``ai, geopolitics, business, environment, politics, tech, sport, arts``;
# ``src/lib/feedBuckets.ts`` ``DesignBucketId``) resolve onto these via
# :data:`DETAIL_CATEGORY_ALIASES` so a ``geopolitics``/``business`` story renders a
# meaningful template instead of the default. ``podcasts`` rides the ``youtube``
# source layout (no SP3 axis of its own).
DetailCategory = Literal[
    "world",
    "markets",
    "tech",
    "sport",
    "culture",
    "youtube",
    "podcasts",
    "x",
]

# The kind of panel a slot renders. ``timeline`` / ``coverage`` read their own
# tables; ``analytic`` reads one ``story_analytics`` row.
PanelKind = Literal["timeline", "coverage", "analytic"]


@dataclass(frozen=True)
class PanelSpec:
    """One panel in a category's Detail template.

    Exactly one of the kind-specific fields is set:
      - ``panel_kind == "timeline"`` → no extra fields.
      - ``panel_kind == "coverage"`` → ``coverage_mode`` set.
      - ``panel_kind == "analytic"`` → ``analytic_kind`` + ``analytic_tab_label`` set.

    Attributes:
        panel_kind: Which renderer this slot uses.
        analytic_kind: The analytic kind to draft + persist (analytic panels only).
        analytic_tab_label: The fixed tab label (analytic panels only).
        coverage_mode: How the Coverage strip is framed (coverage panels only).
    """

    panel_kind: PanelKind
    analytic_kind: AnalyticKind | None = None
    analytic_tab_label: str | None = None
    coverage_mode: CoverageMode | None = None


def _timeline() -> PanelSpec:
    """Build a timeline panel spec."""
    return PanelSpec(panel_kind="timeline")


def _coverage(mode: CoverageMode) -> PanelSpec:
    """Build a coverage panel spec with the given framing."""
    return PanelSpec(panel_kind="coverage", coverage_mode=mode)


def _analytic(kind: AnalyticKind, label: str) -> PanelSpec:
    """Build an analytic panel spec with its kind + fixed tab label."""
    return PanelSpec(
        panel_kind="analytic", analytic_kind=kind, analytic_tab_label=label
    )


# The ordered triple of panels each detail category renders. THE source of truth.
DETAIL_TEMPLATES: dict[DetailCategory, list[PanelSpec]] = {
    "world": [
        _timeline(),
        _analytic("stakes", "STAKES"),
        _coverage("partisan"),
    ],
    "markets": [
        _timeline(),
        _analytic("market_impact", "MARKET IMPACT"),
        _analytic("by_the_numbers", "BY THE NUMBERS"),
    ],
    "tech": [
        _timeline(),
        _analytic("why_it_matters", "WHY IT MATTERS"),
        _analytic("the_concept", "THE CONCEPT"),
    ],
    "sport": [
        _timeline(),
        _analytic("stat_line", "STAT LINE"),
        _analytic("recent_form", "RECENT FORM"),
    ],
    "culture": [
        _timeline(),
        _analytic("subject_profile", "PROFILE"),
        _analytic("why_it_matters", "WHY IT MATTERS"),
    ],
    "youtube": [
        _analytic("source_context", "THE VIDEO"),
        _analytic("key_points", "KEY POINTS"),
        _analytic("implications", "IMPLICATIONS"),
    ],
    "podcasts": [
        _analytic("source_context", "THE EPISODE"),
        _analytic("key_points", "KEY POINTS"),
        _analytic("implications", "IMPLICATIONS"),
    ],
    "x": [
        _analytic("source_context", "THE GIST"),
        _analytic("key_points", "KEY POINTS"),
        _analytic("implications", "IMPLICATIONS"),
    ],
}

# SP3 taxonomy root → Detail-page TEMPLATE key. The SP3 picker roots
# (``src/lib/feedBuckets.ts`` ``DesignBucketId``) do not each own a bespoke panel
# layout; they fold onto the closest existing template so a ``geopolitics`` /
# ``business`` / ``ai`` story renders a meaningful Detail page instead of the
# Culture default. Byte-for-byte twin of ``src/lib/detailTemplates.ts``
# ``DETAIL_CATEGORY_ALIASES`` (Rule 7). Folds (documented):
#   geopolitics/politics/environment → world  (split world_politics keeps the
#     partisan-coverage/world-stakes layout)
#   business → markets   (old ``markets`` fold collapses into business)
#   arts     → culture   (old ``culture`` catch-all renames to arts)
#   ai       → tech      (no bespoke layout; rides the tech why-it-matters layout)
# ``tech``/``sport``/``youtube``/``x`` are already canonical keys (identity, omitted).
DETAIL_CATEGORY_ALIASES: dict[str, DetailCategory] = {
    "geopolitics": "world",
    "politics": "world",
    "environment": "world",
    "business": "markets",
    "arts": "culture",
    "ai": "tech",
}

# feed_category (``categories.py`` ``FeedCategory`` + the frontend-only
# ``podcasts``) → detail category. Carries BOTH the legacy folded enum values
# (``world_politics`` etc., retained-unused in Postgres) AND the SP3 roots
# (``geopolitics``/``business``/...), so a story persisted under EITHER taxonomy
# resolves a template. The source buckets pass through unchanged.
_FEED_CATEGORY_TO_DETAIL: dict[str, DetailCategory] = {
    # Legacy folded enum values (pre-SP3).
    "world_politics": "world",
    "tech_science": "tech",
    "markets": "markets",
    "culture": "culture",
    "podcasts": "podcasts",
    # SP3 picker roots (post-SP3) — fold onto their borrowed template.
    "geopolitics": "world",
    "politics": "world",
    "environment": "world",
    "business": "markets",
    "arts": "culture",
    "ai": "tech",
    # Identity keys shared by both taxonomies.
    "tech": "tech",
    "sport": "sport",
    "youtube": "youtube",
    "x": "x",
}

# story_segment_slug → detail category. Carries BOTH the legacy 5-valued segment
# enum (``geopolitics``/``markets``/``tech``/``sport``/``wildcard``) AND the SP3
# roots the segment backfill added (``ai``/``business``/``environment``/
# ``politics``/``arts``), so a story persisted under EITHER taxonomy resolves a
# template. ``wildcard``/``arts`` are the Culture catch-all (matches
# ``categories.SLUG_TO_CATEGORY``). Source categories (youtube/podcasts/x) are NOT
# reachable from a segment — source ingestion (phase-5d) sets the detail category
# directly from the source type.
_SEGMENT_TO_DETAIL: dict[str, DetailCategory] = {
    # Legacy 5-valued segment enum (pre-SP3).
    "geopolitics": "world",
    "markets": "markets",
    "tech": "tech",
    "sport": "sport",
    "wildcard": "culture",
    # SP3 roots the segment backfill added (post-SP3) — fold onto their template.
    "ai": "tech",
    "business": "markets",
    "environment": "world",
    "politics": "world",
    "arts": "culture",
}

# Best-fit fallback when a category is unknown/empty — Culture is the long-tail
# catch-all (mirrors ``categories.DEFAULT_CATEGORY``).
DEFAULT_DETAIL_CATEGORY: DetailCategory = "culture"


def detail_category_for_segment(segment_slug: str, is_breaking: bool = False) -> DetailCategory:
    """Resolve a topic story's Detail category from its segment (deterministic).

    The persist/orchestrator path already computes ``story_segment_slug``, so this
    is the call site used for GDELT topic stories. The segment maps to its topic
    detail category, falling back to Culture for an unknown segment.

    ``is_breaking`` is accepted for backward-compat with existing callers but is
    IGNORED — the ``breaking`` detail template was removed in phase-SP1 (the
    velocity signal lives on in ``CoverageMomentum`` / ``story_is_breaking``, not as
    a Detail-page bucket).

    Args:
        segment_slug: The story's ``story_segment_slug`` ('geopolitics' / 'markets' /
            'tech' / 'sport' / 'wildcard', or any other value).
        is_breaking: Accepted for backward-compat; ignored (phase-SP1).

    Returns:
        The locked detail category whose template the Detail page should render.

    Example:
        >>> detail_category_for_segment("geopolitics")
        'world'
        >>> detail_category_for_segment("wildcard")
        'culture'
    """
    return _SEGMENT_TO_DETAIL.get(segment_slug, DEFAULT_DETAIL_CATEGORY)


def detail_category_for(feed_category: str | None, is_breaking: bool = False) -> DetailCategory:
    """Resolve a story's Detail-page category (deterministic — Rule 5).

    The story's ``feed_category`` maps to its detail category; an unknown/empty
    category falls back to :data:`DEFAULT_DETAIL_CATEGORY` so every story is always
    renderable.

    ``is_breaking`` is accepted for backward-compat with existing callers but is
    IGNORED — the ``breaking`` detail template was removed in phase-SP1.

    Args:
        feed_category: The story's screen category (``categories.category_for_slug``
            output, e.g. ``"world_politics"`` / ``"markets"`` / ``"youtube"``), or
            ``None`` when no category signal is available.
        is_breaking: Accepted for backward-compat; ignored (phase-SP1).

    Returns:
        The locked detail category whose template the Detail page should render.

    Example:
        >>> detail_category_for("world_politics")
        'world'
        >>> detail_category_for(None)
        'culture'
    """
    if feed_category is None:
        return DEFAULT_DETAIL_CATEGORY
    return _FEED_CATEGORY_TO_DETAIL.get(feed_category, DEFAULT_DETAIL_CATEGORY)


def analytic_panel_specs(detail_category: DetailCategory) -> list[PanelSpec]:
    """Return the ``analytic`` panel specs of a category's template, in slot order.

    These are exactly the panels the enrichment LLM must draft; each one's index in
    the returned list is its ``analytic_slot_index`` at persist time.

    Args:
        detail_category: The resolved detail category.

    Returns:
        The analytic PanelSpecs (timeline/coverage filtered out), in order.

    Example:
        >>> [p.analytic_kind for p in analytic_panel_specs("markets")]
        ['market_impact', 'by_the_numbers']
    """
    return [
        spec
        for spec in DETAIL_TEMPLATES[detail_category]
        if spec.panel_kind == "analytic"
    ]
