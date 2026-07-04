"""The deterministic INTERESTS-phase state machine (spec §2/§5) — pure, no LLM, no I/O.

The chat's INTERESTS phase is a *table-driven* progression the server replays from the
stateless conversation transcript (Rule 5 — the model never decides the flow):

    roots (turn 1)
      -> one multi-select SUB-NICHE turn per lit root
           (skip the first follow-up of a category -> CATEGORY-SKIP offer;
            two categories skipped in a row -> BUILD-FEED offer)
      -> exactly one open-ended WHO drill per selected sub-niche
           (compressed into <= MAX_WHO_DRILLS drills when selections explode)
      -> terminal

Only the SUB-NICHE turns and the terminal extraction call Gemini; every offer and WHO
drill is engine-worded. :func:`plan_interest_turn` replays the transcript and returns the
next :class:`InterestPlan` plus the accumulated selections and deferred-skip records —
:mod:`agents.interview.engine` turns that plan into a typed turn response.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from agents.interview.constants import (
    BUILD_FEED_ACCEPT_LABEL,
    CATEGORY_SKIP_ACCEPT_LABEL,
    MAX_WHO_DRILLS,
)
from agents.interview.guards import lit_roots_from_state, real_taps
from agents.interview.models import DeferredQuestion, InterviewExchange
from agents.shared.logger import get_logger

logger = get_logger("interview.phases")

# The turn kinds the INTERESTS state machine can be poised to serve next.
InterestPhase = Literal[
    "subniche",
    "category_skip_offer",
    "build_feed_offer",
    "who_drill",
    "combined_who_drill",
    "terminal",
]


@dataclass
class SubnicheSelection:
    """One sub-niche the user selected (tapped chip or typed custom) under a lit root.

    Attributes:
        root_slug: The lit root the selection belongs to.
        label: The user-vocabulary sub-niche label (tapped or typed).
    """

    root_slug: str
    label: str


@dataclass
class InterestPlan:
    """What the engine must serve next, plus the state replayed from the transcript.

    Attributes:
        next_phase: The turn kind to serve now.
        lit_roots: The roots tapped on turn 1, in tap order.
        active_root: The root a ``subniche`` / ``category_skip_offer`` turn is about.
        drill_selection: The sub-niche a ``who_drill`` turn is about.
        combined_labels: The folded sub-niche labels for a ``combined_who_drill`` turn.
        selections: Every sub-niche selection replayed so far (order preserved).
        deferred: Every skipped question recorded so far (spec §5/§6).
    """

    next_phase: InterestPhase
    lit_roots: list[str]
    active_root: str | None = None
    drill_selection: SubnicheSelection | None = None
    combined_labels: list[str] = field(default_factory=list)
    selections: list[SubnicheSelection] = field(default_factory=list)
    deferred: list[DeferredQuestion] = field(default_factory=list)


def _engaged(exchange: InterviewExchange) -> bool:
    """Return whether the user engaged a turn (tapped a real option OR typed something)."""
    typed = bool(exchange.free_text_entered and exchange.free_text_entered.strip())
    return bool(real_taps(exchange)) or typed


def _tapped_label(exchange: InterviewExchange, label: str) -> bool:
    """Return whether the user tapped a specific control label this turn (case-insensitive).

    The stateless replay detects a control choice by matching the fixed offer-label
    constants against the labels the client echoes back. Render and match run server-side in
    the same request cycle, so a label minted this turn is matched next turn against the same
    constant — the only drift window is a server redeploy that renames a constant BETWEEN a
    client render and its echo, which is acceptable for the sessionless contract.
    """
    target = label.strip().casefold()
    return any(
        tapped.strip().casefold() == target for tapped in exchange.bubbles_tapped
    )


@dataclass
class WhoDrill:
    """A single open WHO drill about one selected sub-niche."""

    selection: SubnicheSelection


@dataclass
class CombinedWhoDrill:
    """The one folded WHO drill covering every sub-niche past the drill cap."""

    selections: list[SubnicheSelection]


# One entry in the WHO-drill plan: an individual drill or the single combined drill.
DrillSpec = WhoDrill | CombinedWhoDrill


def build_drill_specs(selections: list[SubnicheSelection]) -> list[DrillSpec]:
    """Compute the WHO-drill plan: one drill per selection, compressed past the cap (spec §5).

    Up to :data:`MAX_WHO_DRILLS` selections each get their own open drill. Beyond that, the
    first ``MAX_WHO_DRILLS - 1`` get individual drills and every remaining selection folds
    into ONE combined drill — so the total never exceeds ``MAX_WHO_DRILLS`` and the user
    never faces 40 questions.

    Args:
        selections: The sub-niche selections, in the order they were made.

    Returns:
        A list of :class:`WhoDrill`, with at most one trailing :class:`CombinedWhoDrill`.
    """
    if len(selections) <= MAX_WHO_DRILLS:
        return [WhoDrill(selection) for selection in selections]
    individual = selections[: MAX_WHO_DRILLS - 1]
    remainder = selections[MAX_WHO_DRILLS - 1 :]
    specs: list[DrillSpec] = [WhoDrill(selection) for selection in individual]
    specs.append(CombinedWhoDrill(remainder))
    return specs


def plan_interest_turn(conversation_state: list[InterviewExchange]) -> InterestPlan:
    """Replay the transcript and return the next INTERESTS turn to serve (deterministic).

    Assumes turn 1 (the roots turn) has been answered and at least one root is lit — the
    engine owns the empty / skip-everything / typed-only cases before delegating here.

    Args:
        conversation_state: The ordered prior exchanges (turn 1 first).

    Returns:
        The :class:`InterestPlan` describing the next turn plus replayed state.
    """
    lit_roots = lit_roots_from_state(conversation_state)
    exchanges = conversation_state
    total = len(exchanges)
    selections: list[SubnicheSelection] = []
    deferred: list[DeferredQuestion] = []

    # ── Sub-niche stage: one multi-select turn per lit root, with skip fast-forward. ──
    idx = 1
    root_ptr = 0
    awaiting: InterestPhase = "subniche"
    consecutive_category_skips = 0

    while True:
        at_offer = awaiting in ("category_skip_offer", "build_feed_offer")
        if root_ptr >= len(lit_roots) and not at_offer:
            break  # every category handled → move to the WHO-drill stage
        active_root = lit_roots[root_ptr] if root_ptr < len(lit_roots) else None

        if idx >= total:
            # The next turn is unanswered — this is the one the engine must serve now.
            return InterestPlan(
                next_phase=awaiting,
                lit_roots=lit_roots,
                active_root=active_root,
                selections=selections,
                deferred=deferred,
            )

        exchange = exchanges[idx]

        if awaiting == "subniche":
            if _engaged(exchange):
                for label in real_taps(exchange):
                    selections.append(SubnicheSelection(active_root or "", label))
                typed = (exchange.free_text_entered or "").strip()
                if typed:
                    selections.append(SubnicheSelection(active_root or "", typed))
                consecutive_category_skips = 0
                root_ptr += 1
                idx += 1
            else:
                # Skipped the first follow-up of this category → offer to skip the category.
                deferred.append(
                    DeferredQuestion(
                        deferral_kind="subniche_skip",
                        question_text=exchange.question_text,
                        root_slug=active_root,
                    )
                )
                awaiting = "category_skip_offer"
                idx += 1
        elif awaiting == "category_skip_offer":
            if _tapped_label(exchange, CATEGORY_SKIP_ACCEPT_LABEL):
                deferred.append(
                    DeferredQuestion(
                        deferral_kind="category_skip",
                        question_text=exchange.question_text,
                        root_slug=active_root,
                    )
                )
                consecutive_category_skips += 1
                root_ptr += 1
                idx += 1
                awaiting = (
                    "build_feed_offer"
                    if consecutive_category_skips >= 2
                    else "subniche"
                )
            else:
                # "Show me options" (or anything else) → re-ask this category's sub-niches.
                consecutive_category_skips = 0
                awaiting = "subniche"
                idx += 1
        else:  # awaiting == "build_feed_offer"
            if _tapped_label(exchange, BUILD_FEED_ACCEPT_LABEL):
                return InterestPlan(
                    next_phase="terminal",
                    lit_roots=lit_roots,
                    selections=selections,
                    deferred=deferred,
                )
            # "Keep exploring" → resume the next category (or fall through to WHO stage).
            consecutive_category_skips = 0
            awaiting = "subniche"
            idx += 1

    # ── WHO-drill stage: one open drill per selection, compressed past the cap. ──
    for spec in build_drill_specs(selections):
        if idx >= total:
            if isinstance(spec, WhoDrill):
                return InterestPlan(
                    next_phase="who_drill",
                    lit_roots=lit_roots,
                    drill_selection=spec.selection,
                    selections=selections,
                    deferred=deferred,
                )
            return InterestPlan(
                next_phase="combined_who_drill",
                lit_roots=lit_roots,
                combined_labels=[item.label for item in spec.selections],
                selections=selections,
                deferred=deferred,
            )
        exchange = exchanges[idx]
        if not _engaged(exchange):
            if isinstance(spec, WhoDrill):
                # A combined drill spans several roots, so its record carries no single root.
                deferred.append(
                    DeferredQuestion(
                        deferral_kind="who_drill_skip",
                        question_text=exchange.question_text,
                        root_slug=spec.selection.root_slug,
                        subniche_label=spec.selection.label,
                    )
                )
            else:
                deferred.append(
                    DeferredQuestion(
                        deferral_kind="who_drill_skip",
                        question_text=exchange.question_text,
                        subniche_label=", ".join(
                            item.label for item in spec.selections
                        ),
                    )
                )
        idx += 1

    # Every category and every WHO drill has been answered → terminal.
    return InterestPlan(
        next_phase="terminal",
        lit_roots=lit_roots,
        selections=selections,
        deferred=deferred,
    )
