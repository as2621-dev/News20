"""Behavior tests for the TUNE phase — ANGLE + SKIP turns (FSR interview slice #17).

Each test encodes WHY the rule matters (Rule 9): the two TUNE jobs are the founder's
LOCKED contract, so a flow that reaches terminal without a SKIP turn per selected category
is WRONG (AC #1). The wording is LLM-dynamic but must degrade to deterministic copy without
ever dead-ending (AC #3). Options are hard-capped at 6 and the recorded answer is ALWAYS a
real tap / typed term, never invented (AC #4). Mutes/angles ride the terminal schema
additively (AC #2 persist-half). Gemini is faked at the ``call_gemini_json`` boundary.
"""

from __future__ import annotations

import pytest

from agents.interview.constants import (
    ANGLE_FALLBACK_OPTIONS,
    SKIP_BUBBLE_LABEL,
    TYPE_YOUR_OWN_BUBBLE_LABEL,
)
from agents.interview.engine import run_interview_turn
from agents.interview.models import (
    InterviewDecision,
    InterviewExchange,
    InterviewTurnRequest,
)
from agents.interview.phases import (
    SubnicheSelection,
    plan_interest_turn,
    selected_roots_in_order,
)
from agents.pipeline.llm_clients import GeminiJsonResult


class _FakeLLMClient:
    """Returns a canned decision (or raises) at the exact ``call_gemini_json`` boundary."""

    def __init__(
        self, decision: InterviewDecision | None = None, raises: Exception | None = None
    ) -> None:
        self._decision = decision
        self._raises = raises
        self.call_count = 0

    async def call_gemini_json(
        self, prompt: str, response_schema: object, system: str = "", model: str = "m"
    ) -> GeminiJsonResult:
        self.call_count += 1
        if self._raises is not None:
            raise self._raises
        return GeminiJsonResult(
            parsed=self._decision,
            prompt_tokens=11,
            output_tokens=22,
            total_tokens=33,
            elapsed_ms=5,
            model=model,
        )


def _ex(
    question: str,
    tapped: list[str] | None = None,
    typed: str | None = None,
) -> InterviewExchange:
    return InterviewExchange(
        question_text=question,
        bubbles_offered=[],
        bubbles_tapped=tapped or [],
        free_text_entered=typed,
    )


def _drive_to_terminal(
    root_taps: list[str],
    subniche_picks: dict[str, list[str]],
    *,
    angle_tap: str = "Analysis & context",
    mute_typed: str = "",
    max_steps: int = 80,
) -> tuple[list[str], object]:
    """Replay the PURE machine to terminal, answering every turn; return (phases, plan).

    ``subniche_picks`` maps a root slug -> the sub-niche chips tapped for it. TUNE turns
    are answered with ``angle_tap`` (ANGLE) and ``mute_typed`` (SKIP; ``""`` = skipped).
    """
    state = [_ex("What news?", tapped=root_taps)]
    phases: list[str] = []
    for _ in range(max_steps):
        plan = plan_interest_turn(state)
        phases.append(plan.next_phase)
        if plan.next_phase == "terminal":
            return phases, plan
        if plan.next_phase == "subniche":
            state.append(
                _ex(f"sub:{plan.active_root}", tapped=subniche_picks.get(plan.active_root or "", []))
            )
        elif plan.next_phase in ("who_drill", "combined_who_drill"):
            state.append(_ex("who", typed="something"))
        elif plan.next_phase == "angle_tune":
            state.append(_ex("angle", tapped=([angle_tap] if angle_tap else [])))
        elif plan.next_phase == "skip_tune":
            state.append(_ex("skip", typed=(mute_typed or None)))
        else:  # pragma: no cover - the offers are not reached in these fully-engaged flows
            raise AssertionError(f"unexpected phase {plan.next_phase}")
    raise AssertionError(f"machine never terminated; phases={phases}")


# ── AC #1 — founder-locked: every selected category gets BOTH an ANGLE and a SKIP turn ──


def test_each_selected_category_gets_an_angle_and_a_skip_tune_turn() -> None:
    """Two built-out categories → exactly two ANGLE and two SKIP turns, in order.

    WHY: the SKIP job is founder-locked — the machine must emit one SKIP turn per selected
    category no matter what. A flow that reaches terminal missing a SKIP turn is a contract
    breach (a test that passes without SKIP present is wrong).
    """
    phases, _plan = _drive_to_terminal(
        ["Sport", "Tech"],
        {"sport": ["Cricket"], "tech": ["AI chips"]},
    )
    assert phases.count("angle_tune") == 2
    assert phases.count("skip_tune") == 2
    # SKIP always follows its category's ANGLE (deterministic per-category pairing).
    tune_seq = [p for p in phases if p in ("angle_tune", "skip_tune")]
    assert tune_seq == ["angle_tune", "skip_tune", "angle_tune", "skip_tune"]
    assert phases[-1] == "terminal"


def test_a_skipped_category_earns_no_tune_turns() -> None:
    """A category the user never built out (no sub-niche) gets no ANGLE/SKIP turns.

    WHY: TUNE is "per SELECTED category" — a skipped category has no selections, so it
    must not generate phantom tune turns (which would ask about a topic the user dropped).
    """
    # Sport is skipped at its sub-niche turn → category-skip; only Tech is built out.
    state = [
        _ex("What news?", tapped=["Sport", "Tech"]),
        _ex("Which sport?", tapped=[]),  # subniche skip → category-skip offer
    ]
    plan = plan_interest_turn(state)
    assert plan.next_phase == "category_skip_offer"
    # Only tech carries a selection, so only tech is a "selected category".
    assert selected_roots_in_order([SubnicheSelection("tech", "AI chips")]) == ["tech"]


# ── AC #4 — recorded answers are traceable to real taps/typed text (never invented) ──


def test_tune_answers_record_only_real_taps_and_typed_terms() -> None:
    """Terminal mutes/angles equal exactly the user's TUNE taps + typed text, nothing else.

    WHY: mutes drive a HARD assembly filter — a fabricated mute term would silently erase
    stories the user never asked to lose. Every term must retrace to an actual answer.
    """
    _phases, plan = _drive_to_terminal(
        ["Sport"],
        {"sport": ["Cricket"]},
        angle_tap="Numbers & data",
        mute_typed="transfer rumours",
    )
    assert [(a.angle_category, a.angle_label) for a in plan.angles] == [  # type: ignore[attr-defined]
        ("sport", "Numbers & data")
    ]
    assert [(m.mute_category, m.mute_term) for m in plan.mutes] == [  # type: ignore[attr-defined]
        ("sport", "transfer rumours")
    ]


def test_skipped_tune_turns_record_nothing() -> None:
    """Skipping both TUNE turns leaves mutes and angles empty (no invented answers)."""
    _phases, plan = _drive_to_terminal(
        ["Sport"],
        {"sport": ["Cricket"]},
        angle_tap="",  # ANGLE skipped
        mute_typed="",  # SKIP skipped
    )
    assert plan.angles == []  # type: ignore[attr-defined]
    assert plan.mutes == []  # type: ignore[attr-defined]


# ── AC #3 — LLM failure / malformed output → deterministic fallback, never a dead-end ──


@pytest.mark.asyncio
async def test_angle_tune_llm_timeout_degrades_to_deterministic_fallback() -> None:
    """An ANGLE turn whose Gemini call times out serves deterministic wording, not a retry.

    WHY: TUNE turns must never dead-end — a failed LLM degrades the WORDING but the founder's
    job is still served (a question the user can answer), unlike the sub-niche turn's retry.
    """
    fake = _FakeLLMClient(raises=TimeoutError("gemini timed out"))
    # State poised at the ANGLE turn: sport built out + its WHO drill answered.
    state = [
        _ex("What news?", tapped=["Sport"]),
        _ex("Which sport?", tapped=["Cricket"]),
        _ex("Cricket — name one?", typed="IPL"),  # WHO drill answered → ANGLE next
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "question"  # never a retry
    option_labels = [b.bubble_label for b in response.bubbles if b.bubble_kind == "option"]
    assert list(ANGLE_FALLBACK_OPTIONS) == option_labels  # deterministic lenses served


@pytest.mark.asyncio
async def test_skip_tune_llm_failure_still_serves_the_founder_locked_turn() -> None:
    """A SKIP turn whose Gemini call fails still renders (affordance-only), never dropped.

    WHY: SKIP is founder-locked — even with no model options a mute turn must appear so the
    user can type what to mute. Dropping it on an LLM failure would be a silent contract loss.
    """
    fake = _FakeLLMClient(raises=RuntimeError("malformed json"))
    # State poised at the SKIP turn: ANGLE already answered.
    state = [
        _ex("What news?", tapped=["Sport"]),
        _ex("Which sport?", tapped=["Cricket"]),
        _ex("Cricket — name one?", typed="IPL"),
        _ex("How do you read Sport?", tapped=["Analysis & context"]),  # ANGLE answered
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "question"
    kinds = {b.bubble_kind for b in response.bubbles}
    assert "skip" in kinds and "type_your_own" in kinds  # the SKIP turn is present


# ── AC #4 — the ≤6 option cap holds even when the model over-produces ──


@pytest.mark.asyncio
async def test_angle_tune_never_emits_more_than_six_options() -> None:
    """A model returning 10 ANGLE options is capped to 6 (+ skip + type), never exceeded.

    WHY: the ≤6 bubble guardrail is code-enforced, never model-trusted — a chatty model
    must not blow past the design limit.
    """
    over = InterviewDecision(
        action="ask",
        question_text="How do you read Sport?",
        bubbles=[f"lens{i}" for i in range(10)],
    )
    fake = _FakeLLMClient(decision=over)
    state = [
        _ex("What news?", tapped=["Sport"]),
        _ex("Which sport?", tapped=["Cricket"]),
        _ex("Cricket — name one?", typed="IPL"),  # → ANGLE turn
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    options = [b for b in response.bubbles if b.bubble_kind == "option"]
    assert len(options) <= 6
    # The two affordances are still appended after the capped options.
    assert response.bubbles[-2].bubble_label == SKIP_BUBBLE_LABEL
    assert response.bubbles[-1].bubble_label == TYPE_YOUR_OWN_BUBBLE_LABEL


@pytest.mark.asyncio
async def test_terminal_carries_mutes_and_angles_collected_from_taps() -> None:
    """The terminal payload additively carries the mutes + angles gathered across TUNE turns.

    WHY: the terminal schema grows mutes + angles alongside micro-interests — the client
    persists mutes to user_mute_terms and the assembler filters on them. They must survive
    to the terminal response, keyed to their category and traceable to the taps.
    """
    decision = InterviewDecision(action="terminate", micro_interests=[])
    fake = _FakeLLMClient(decision=decision)
    state = [
        _ex("What news?", tapped=["Sport"]),
        _ex("Which sport?", tapped=["Cricket"]),
        _ex("Cricket — name one?", typed="IPL"),
        _ex("How do you read Sport?", tapped=["Numbers & data"]),  # ANGLE
        _ex("Anything to mute?", tapped=["injury gossip"], typed="transfer rumours"),  # SKIP
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "terminal"
    assert [(m.mute_category, m.mute_term) for m in response.mute_terms] == [
        ("sport", "injury gossip"),
        ("sport", "transfer rumours"),
    ]
    assert [(a.angle_category, a.angle_label) for a in response.angle_preferences] == [
        ("sport", "Numbers & data")
    ]
