"""Pure, deterministic interview guardrails — no LLM, no I/O (Rule 5, easy to test).

These functions compute everything the server must own rather than trust to the model:
which roots are lit, how deep each has been drilled, the tap budget, the gibberish steer,
the fallback ladder, and terminal validation/dedup. :mod:`agents.interview.engine` wires
them around the Gemini call.
"""

from __future__ import annotations

import re

from agents.interview.constants import (
    MAX_DRILL_DEPTH_PER_ROOT,
    ROOT_LABEL_BY_SLUG,
    ROOT_SLUG_BY_LABEL,
    SKIP_BUBBLE_LABEL,
    TYPE_YOUR_OWN_BUBBLE_LABEL,
    VALID_ROOT_SLUGS,
)
from agents.interview.models import InterviewExchange, MicroInterest, MicroInterestDraft
from agents.shared.logger import get_logger

logger = get_logger("interview.guards")

# The two always-present affordances, casefolded, so they never count as real taps/options.
META_LABELS: frozenset[str] = frozenset(
    {SKIP_BUBBLE_LABEL.casefold(), TYPE_YOUR_OWN_BUBBLE_LABEL.casefold()}
)
_SLUG_SEGMENT_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class DrillState:
    """Replayed deterministic drill state derived purely from the conversation.

    Attributes:
        lit_roots: Root slugs tapped on turn 1, in tap order.
        drill_counts: root slug -> number of drill turns spent under it.
        active_root: The root currently being drilled, or ``None`` when every lit root
            has hit the depth cap (the signal to steer to terminal).
    """

    def __init__(
        self,
        lit_roots: list[str],
        drill_counts: dict[str, int],
        active_root: str | None,
    ) -> None:
        self.lit_roots = lit_roots
        self.drill_counts = drill_counts
        self.active_root = active_root


def is_meta_label(label: str) -> bool:
    """Return whether a bubble label is one of the always-present skip / type affordances."""
    return label.strip().casefold() in META_LABELS


def real_taps(exchange: InterviewExchange) -> list[str]:
    """Return the exchange's tapped labels excluding the skip / type-your-own affordances."""
    return [label for label in exchange.bubbles_tapped if not is_meta_label(label)]


def lit_roots_from_state(conversation_state: list[InterviewExchange]) -> list[str]:
    """Resolve the roots the user tapped on turn 1 to their slugs, in tap order.

    Only the first exchange offers roots, so only its taps can light a root. Unknown
    labels (e.g. a deeper bubble that coincidentally matches) are ignored.

    Args:
        conversation_state: The ordered prior exchanges.

    Returns:
        The lit root slugs, de-duplicated, preserving first-tap order.
    """
    if not conversation_state:
        return []
    seen: list[str] = []
    for label in real_taps(conversation_state[0]):
        slug = ROOT_SLUG_BY_LABEL.get(label.strip().casefold())
        if slug and slug not in seen:
            seen.append(slug)
    return seen


def total_taps(conversation_state: list[InterviewExchange]) -> int:
    """Count real (non-meta) bubble taps across the whole conversation (the tap budget)."""
    return sum(len(real_taps(exchange)) for exchange in conversation_state)


def free_text_only_turn_count(conversation_state: list[InterviewExchange]) -> int:
    """Count turns where the user typed free text and tapped nothing real (the gibberish steer).

    Two such turns means we already asked one clarifying question — the engine then parks
    rather than looping on unmappable input (spec §3).
    """
    return sum(
        1
        for exchange in conversation_state
        if exchange.free_text_entered and not real_taps(exchange)
    )


def replay_drill_state(conversation_state: list[InterviewExchange]) -> DrillState:
    """Replay the conversation into deterministic drill state (lit roots, depths, active root).

    Turn 1 lights the roots; every later exchange with a real tap is a drill turn under the
    active root. When a root reaches :data:`MAX_DRILL_DEPTH_PER_ROOT` the active root advances
    to the next lit root not yet at the cap; when none remain, ``active_root`` is ``None``.

    Args:
        conversation_state: The ordered prior exchanges.

    Returns:
        The computed :class:`DrillState`.
    """
    lit_roots = lit_roots_from_state(conversation_state)
    drill_counts: dict[str, int] = {root: 0 for root in lit_roots}

    def _next_active() -> str | None:
        for root in lit_roots:
            if drill_counts[root] < MAX_DRILL_DEPTH_PER_ROOT:
                return root
        return None

    active_root = _next_active()
    for exchange in conversation_state[1:]:
        if active_root is None:
            break
        if real_taps(exchange):
            drill_counts[active_root] += 1
            if drill_counts[active_root] >= MAX_DRILL_DEPTH_PER_ROOT:
                active_root = _next_active()
    return DrillState(
        lit_roots=lit_roots, drill_counts=drill_counts, active_root=active_root
    )


def derive_ladder(canonical_slug: str) -> list[str]:
    """Derive the fallback ladder: the PARENT chain of a dotted slug (excludes the leaf).

    ``sport.cricket.ipl.auctions`` -> ``["sport", "sport.cricket", "sport.cricket.ipl"]``
    (spec §4 — the ordered parent chain the M3 assembler climbs one level at a time; the
    direct-tagged slug itself is not in the ladder). A root-level slug has no parents, so
    its ladder is empty. The server owns the ladder (not the model) so it is always
    consistent with the slug.

    Args:
        canonical_slug: A validated dotted slug.

    Returns:
        The ordered parent chain from the root segment up to (but excluding) the leaf slug.
    """
    segments = canonical_slug.split(".")
    return [".".join(segments[: i + 1]) for i in range(len(segments) - 1)]


def _normalize_slug(canonical_slug: str) -> str:
    """Normalize a model-proposed slug's separators before shape validation.

    Gemini reliably names the RIGHT concept but is sloppy with separators — the M4
    day-1 persona runs saw ``ai.foundation_models`` and ``geopolitics.tsmc.chip_foundry``
    (underscores) dropped by the shape guard, parking real users on roots-only profiles.
    Underscores / spaces are deterministic spelling variance, not semantic invention, so
    code (Rule 5) folds them to the canonical dash form: lowercase, ``_``/whitespace runs
    → ``-``, repeated dashes collapsed, edge dashes stripped per segment. Anything still
    malformed after this (bad root, empty segment, symbols) is dropped as before.

    Example:
        >>> _normalize_slug("ai.Foundation_Models")
        'ai.foundation-models'
    """
    slug = canonical_slug.strip().lower()
    slug = re.sub(r"[_\s]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return ".".join(segment.strip("-") for segment in slug.split("."))


def _is_valid_slug(canonical_slug: str) -> bool:
    """Return whether a slug is a root-anchored dotted slug with well-formed segments."""
    if not canonical_slug:
        return False
    segments = canonical_slug.split(".")
    if segments[0] not in VALID_ROOT_SLUGS:
        return False
    return all(_SLUG_SEGMENT_RE.match(segment) for segment in segments)


def validate_and_dedup(
    drafts: list[MicroInterestDraft],
    lit_roots: list[str],
    had_free_text: bool,
) -> list[MicroInterest]:
    """Validate model-proposed interests and dedup by canonical slug — dropping the invalid loudly.

    An interest survives only if: its slug is root-anchored and well-formed; it is
    traceable; it has a non-empty display label; and it carries >= 2 distinct anchor terms.
    Traceability: an interest under a LIT root (the user tapped it) is allowed at any depth;
    an interest under a NON-lit root is allowed ONLY when the user typed free text AND the
    slug is root-level — so typed/unmappable input can park at a root but the model can
    never fabricate a deep niche under a root the user never chose (spec §3). The ladder is
    re-derived in code. Invalid drafts are logged with a ``fix_suggestion`` and dropped —
    never silently minted.

    Args:
        drafts: The model-proposed drafts.
        lit_roots: The roots the user actually tapped (traceability set).
        had_free_text: Whether the user typed anything this interview (widens traceability).

    Returns:
        The validated, de-duplicated micro-interests (order preserved).
    """
    lit_root_set = set(lit_roots)
    validated: list[MicroInterest] = []
    seen_slugs: set[str] = set()
    for draft in drafts:
        slug = _normalize_slug(draft.canonical_slug)
        label = draft.display_label.strip()
        anchor_terms = _distinct_nonempty(draft.search_anchor_terms)
        drop_reason = _draft_drop_reason(
            slug, label, anchor_terms, lit_root_set, had_free_text
        )
        if drop_reason:
            logger.warning(
                "interview_interest_dropped",
                canonical_slug=slug or "<empty>",
                reason=drop_reason,
                fix_suggestion="Model proposed an unanchored/unsupported interest; re-ask the turn.",
            )
            continue
        if slug in seen_slugs:
            logger.info("interview_interest_deduped", canonical_slug=slug)
            continue
        seen_slugs.add(slug)
        validated.append(
            MicroInterest(
                display_label=label,
                canonical_slug=slug,
                ladder=derive_ladder(slug),
                search_anchor_terms=anchor_terms,
                strict=draft.strict,
            )
        )
    return validated


def _draft_drop_reason(
    slug: str,
    label: str,
    anchor_terms: list[str],
    lit_root_set: set[str],
    had_free_text: bool,
) -> str | None:
    """Return why a draft interest must be dropped, or ``None`` when it is valid."""
    if not _is_valid_slug(slug):
        return "slug_not_root_anchored"
    if not label:
        return "empty_display_label"
    if len(anchor_terms) < 2:
        return "under_two_anchor_terms"
    if slug.split(".")[0] not in lit_root_set:
        # A non-lit root is only reachable via typed text, and then only as a root-level
        # park — never a fabricated deep niche under a root the user never tapped.
        if not had_free_text:
            return "untraceable_to_tap_or_text"
        if "." in slug:
            return "untraced_niche_not_root_level"
    return None


def _distinct_nonempty(terms: list[str]) -> list[str]:
    """Strip, drop empties, and de-duplicate case-insensitively while preserving order."""
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip()
        key = cleaned.casefold()
        if cleaned and key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result


def roots_only_interests(lit_roots: list[str]) -> list[MicroInterest]:
    """Build root-level interests for the lit roots — the fallback when no niche validates."""
    interests: list[MicroInterest] = []
    for root in lit_roots:
        label = ROOT_LABEL_BY_SLUG[root]
        interests.append(
            MicroInterest(
                display_label=label,
                canonical_slug=root,
                ladder=[],  # a root has no parents to climb (spec §4)
                search_anchor_terms=[label, f"{label} news"],
                strict=False,
            )
        )
    return interests
