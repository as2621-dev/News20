"""Pure builders for the typed interview turn responses — no LLM, no I/O, no logging.

Every deterministic turn shape the engine can emit (the fixed roots, an LLM-worded ask,
the engine-worded skip fast-forward offers and WHO drills, the terminal payload, and the
graceful retry body) is constructed here so :mod:`agents.interview.engine` stays focused on
orchestration (Rule 5 — agent logic is highly modular, files stay small).
"""

from __future__ import annotations

import time

from agents.interview.constants import (
    BUILD_FEED_ACCEPT_LABEL,
    BUILD_FEED_DECLINE_LABEL,
    CATEGORY_SKIP_ACCEPT_LABEL,
    CATEGORY_SKIP_DECLINE_LABEL,
    MAX_OPTION_BUBBLES,
    ROOT_BUBBLES,
    ROOT_LABEL_BY_SLUG,
    ROOT_QUESTION_TEXT,
    SKIP_BUBBLE_LABEL,
    TYPE_YOUR_OWN_BUBBLE_LABEL,
    WHO_DRILL_NOTHING_LABEL,
)
from agents.interview.guards import META_LABELS
from agents.interview.models import (
    DeferredQuestion,
    InterviewBubble,
    InterviewExchange,
    InterviewTurnCost,
    InterviewTurnResponse,
    MicroInterest,
)

# The most sub-niche labels a combined WHO drill names before eliding — keeps the drill
# question short and inside the response's question_text however many folded in.
_MAX_COMBINED_LABELS_SHOWN = 6


def no_llm_cost(start_time: float) -> InterviewTurnCost:
    """Cost record for a deterministic (no-LLM) turn: zero tokens, real wall time."""
    return InterviewTurnCost(wall_time_ms=int((time.monotonic() - start_time) * 1000))


def sanitize_option_bubbles(
    proposed_labels: list[str], conversation_state: list[InterviewExchange]
) -> list[InterviewBubble]:
    """Clean model option labels: drop meta/dupes/already-tapped, cap at 6, append skip + type.

    Args:
        proposed_labels: The raw option labels the model proposed.
        conversation_state: Prior exchanges (used to avoid re-offering tapped labels).

    Returns:
        The rendered bubbles: <= 6 options followed by the skip and type-your-own affordances.
        The two affordances are always appended even when no options survive (the caller
        treats a zero-option turn as a failure).
    """
    already_tapped = {
        label.strip().casefold()
        for exchange in conversation_state
        for label in exchange.bubbles_tapped
    }
    options: list[InterviewBubble] = []
    seen: set[str] = set()
    for raw_label in proposed_labels:
        label = raw_label.strip()
        key = label.casefold()
        if not label or key in META_LABELS or key in already_tapped or key in seen:
            continue
        seen.add(key)
        options.append(InterviewBubble(bubble_label=label, bubble_kind="option"))
        if len(options) >= MAX_OPTION_BUBBLES:
            break
    options.append(InterviewBubble(bubble_label=SKIP_BUBBLE_LABEL, bubble_kind="skip"))
    options.append(
        InterviewBubble(
            bubble_label=TYPE_YOUR_OWN_BUBBLE_LABEL, bubble_kind="type_your_own"
        )
    )
    return options


def question_response(
    question_text: str,
    bubbles: list[InterviewBubble],
    turn_index: int,
    cost: InterviewTurnCost,
) -> InterviewTurnResponse:
    """Build a non-terminal question response with the given (already-rendered) bubbles."""
    return InterviewTurnResponse(
        response_kind="question",
        turn_index=turn_index,
        question_text=question_text,
        bubbles=bubbles,
        turn_cost=cost,
    )


def root_question_response(turn_index: int, start_time: float) -> InterviewTurnResponse:
    """Build the deterministic turn-1 response: the 8 fixed roots + skip + type (no LLM)."""
    bubbles = [
        InterviewBubble(bubble_label=label, bubble_kind="option")
        for _, label in ROOT_BUBBLES
    ]
    bubbles.append(InterviewBubble(bubble_label=SKIP_BUBBLE_LABEL, bubble_kind="skip"))
    bubbles.append(
        InterviewBubble(
            bubble_label=TYPE_YOUR_OWN_BUBBLE_LABEL, bubble_kind="type_your_own"
        )
    )
    return question_response(
        ROOT_QUESTION_TEXT, bubbles, turn_index, no_llm_cost(start_time)
    )


def category_skip_offer_response(
    active_root: str | None, turn_index: int, start_time: float
) -> InterviewTurnResponse:
    """The deterministic category-skip offer (spec §5): skip this topic, or see options."""
    label = ROOT_LABEL_BY_SLUG.get(active_root or "", active_root or "this topic")
    bubbles = [
        InterviewBubble(bubble_label=CATEGORY_SKIP_DECLINE_LABEL, bubble_kind="option"),
        InterviewBubble(bubble_label=CATEGORY_SKIP_ACCEPT_LABEL, bubble_kind="skip"),
    ]
    return question_response(
        f"No rush on {label} — want to see a few options, or skip it for now?",
        bubbles,
        turn_index,
        no_llm_cost(start_time),
    )


def build_feed_offer_response(
    turn_index: int, start_time: float
) -> InterviewTurnResponse:
    """The deterministic 'just build my feed' offer after two categories skipped (spec §5)."""
    bubbles = [
        InterviewBubble(bubble_label=BUILD_FEED_DECLINE_LABEL, bubble_kind="option"),
        InterviewBubble(bubble_label=BUILD_FEED_ACCEPT_LABEL, bubble_kind="skip"),
    ]
    return question_response(
        "Want me to just build your feed from what you've picked so far? "
        "You can always fine-tune it later.",
        bubbles,
        turn_index,
        no_llm_cost(start_time),
    )


def _who_drill_bubbles() -> list[InterviewBubble]:
    """The two affordances on every open WHO drill: 'Nothing specific →' + type-your-own."""
    return [
        InterviewBubble(bubble_label=WHO_DRILL_NOTHING_LABEL, bubble_kind="skip"),
        InterviewBubble(
            bubble_label=TYPE_YOUR_OWN_BUBBLE_LABEL, bubble_kind="type_your_own"
        ),
    ]


def who_drill_response(
    subniche_label: str, turn_index: int, start_time: float
) -> InterviewTurnResponse:
    """One open-ended WHO drill for a selected sub-niche (spec §2), engine-worded."""
    return question_response(
        f"{subniche_label} — a specific series, team, org, or person you follow? "
        "Name one, or skip.",
        _who_drill_bubbles(),
        turn_index,
        no_llm_cost(start_time),
    )


def combined_who_drill_response(
    combined_labels: list[str], turn_index: int, start_time: float
) -> InterviewTurnResponse:
    """The single folded WHO drill for the sub-niches past the drill cap (spec §5)."""
    shown = combined_labels[:_MAX_COMBINED_LABELS_SHOWN]
    joined = ", ".join(shown)
    if len(combined_labels) > _MAX_COMBINED_LABELS_SHOWN:
        joined = f"{joined}, and more"
    return question_response(
        f"Anything specific within {joined}? Name what matters most, or skip.",
        _who_drill_bubbles(),
        turn_index,
        no_llm_cost(start_time),
    )


def terminal_response(
    interests: list[MicroInterest],
    roots_only_fallback: bool,
    deferred: list[DeferredQuestion],
    turn_index: int,
    cost: InterviewTurnCost,
) -> InterviewTurnResponse:
    """Build a terminal response carrying the validated interests + deferred-skip records."""
    return InterviewTurnResponse(
        response_kind="terminal",
        turn_index=turn_index,
        micro_interests=interests,
        roots_only_fallback=roots_only_fallback,
        deferred_questions=deferred,
        turn_cost=cost,
    )


def retry_response(turn_index: int, cost: InterviewTurnCost) -> InterviewTurnResponse:
    """Build the typed retry body (HTTP 200) the client re-asks the same turn from."""
    return InterviewTurnResponse(
        response_kind="retry",
        turn_index=turn_index,
        error_code="interview_turn_unavailable",
        error_message="The interview could not generate this turn. Please try again.",
        retry_hint="Re-send the same conversation state to retry this turn.",
        turn_cost=cost,
    )
