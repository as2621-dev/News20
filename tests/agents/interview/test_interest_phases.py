"""Behavior tests for the INTERESTS-phase state machine (FSR interview slice #14).

Each test encodes WHY the rule matters (Rule 9): multi-select must never collapse to one
path; every selected sub-niche earns exactly one WHO drill; an explosion of picks must be
COMPRESSED, never turned into 40 questions; the skip fast-forward is engine-owned and
deterministic (never model judgment); and every skip is recorded as deferred so a
fast-follow surface can resurface it. The pure machine is driven with real inputs (no LLM);
the engine-seam tests fake Gemini at ``LLMClient.call_gemini_json``.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from agents.interview.constants import (
    BUILD_FEED_ACCEPT_LABEL,
    BUILD_FEED_DECLINE_LABEL,
    CATEGORY_SKIP_ACCEPT_LABEL,
    CATEGORY_SKIP_DECLINE_LABEL,
    MAX_WHO_DRILLS,
    TYPE_YOUR_OWN_BUBBLE_LABEL,
    WHO_DRILL_NOTHING_LABEL,
)
from agents.interview.engine import run_interview_turn
from agents.interview.models import (
    InterviewDecision,
    InterviewExchange,
    InterviewTurnRequest,
    MicroInterestDraft,
)
from agents.interview.phases import (
    CombinedWhoDrill,
    SubnicheSelection,
    WhoDrill,
    build_drill_specs,
    plan_interest_turn,
)
from agents.pipeline.llm_clients import GeminiJsonResult


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


# A per-root sub-niche responder: root slug -> (taps, typed-or-None).
SubnicheResponder = Callable[[str | None], tuple[list[str], str | None]]


def _play(
    root_taps: list[str],
    subniche: SubnicheResponder,
    *,
    who: str = "something specific",
    category: str = "skip",
    build: str = "accept",
    angle: str = "Analysis & context",
    mute: str = "",
    max_steps: int = 80,
) -> tuple[list[str], object]:
    """Drive the PURE phase machine to terminal; return (phase_sequence, final_plan).

    ``subniche`` answers each sub-niche turn; ``who`` is the typed WHO-drill answer (``""``
    skips it); ``category`` is ``skip``/``show`` for a category offer; ``build`` is
    ``accept``/``decline`` for a build-feed offer; ``angle`` is the tapped ANGLE-TUNE lens
    (``""`` skips it); ``mute`` is the typed SKIP-TUNE term (``""`` skips it — the default,
    so pre-TUNE tests are unaffected by what a mute would record).
    """
    state = [_ex("What news?", tapped=root_taps)]
    phases: list[str] = []
    for _ in range(max_steps):
        plan = plan_interest_turn(state)
        phases.append(plan.next_phase)
        if plan.next_phase == "terminal":
            return phases, plan
        if plan.next_phase == "subniche":
            taps, typed = subniche(plan.active_root)
            state.append(_ex(f"sub:{plan.active_root}", tapped=taps, typed=typed))
        elif plan.next_phase == "angle_tune":
            state.append(_ex("angle", tapped=([angle] if angle else [])))
        elif plan.next_phase == "skip_tune":
            state.append(_ex("skip", typed=(mute or None)))
        elif plan.next_phase in ("who_drill", "combined_who_drill"):
            state.append(_ex("who", typed=(who or None)))
        elif plan.next_phase == "category_skip_offer":
            label = (
                CATEGORY_SKIP_ACCEPT_LABEL
                if category == "skip"
                else CATEGORY_SKIP_DECLINE_LABEL
            )
            state.append(_ex("category offer", tapped=[label]))
        elif plan.next_phase == "build_feed_offer":
            label = (
                BUILD_FEED_ACCEPT_LABEL
                if build == "accept"
                else BUILD_FEED_DECLINE_LABEL
            )
            state.append(_ex("build offer", tapped=[label]))
    raise AssertionError(f"machine never terminated; phases={phases}")


# ── AC #1: multi-select never collapses; one WHO drill per selected sub-niche ──


def test_multi_select_yields_exactly_one_who_drill_per_selected_subniche() -> None:
    """3 roots + several sub-niches each → one WHO drill per selection, terminal after.

    WHY: this is the marquee failure mode — if multi-select silently collapsed to one path
    the user would be interviewed about only one interest and the rest would vanish.
    """
    picks = {
        "sport": (["Cricket", "Tennis"], None),
        "tech": (["AI chips"], None),
        "business": (["Startups"], None),
    }
    phases, plan = _play(["Sport", "Tech", "Business"], lambda root: picks[root or ""])

    assert phases.count("subniche") == 3  # one multi-select turn per lit root
    assert phases.count("who_drill") == 4  # one open drill per selected sub-niche
    assert phases[-1] == "terminal"
    assert [(s.root_slug, s.label) for s in plan.selections] == [  # type: ignore[attr-defined]
        ("sport", "Cricket"),
        ("sport", "Tennis"),
        ("tech", "AI chips"),
        ("business", "Startups"),
    ]


# ── AC #2: drill-count compression when selections explode ──


def test_drill_count_is_capped_and_remainder_folds_into_one_combined_drill() -> None:
    """12 sub-niches → 7 individual drills + 1 combined drill (never 12), then terminal.

    WHY: exactly-one-per-sub-niche must not become 40 questions — the spec caps drills and
    folds the tail into a single combined drill.
    """
    phases, _plan = _play(
        ["Sport", "Tech", "Business"],
        lambda root: ([f"{root}-a", f"{root}-b", f"{root}-c", f"{root}-d"], None),
    )

    assert phases.count("who_drill") == MAX_WHO_DRILLS - 1  # 7 individual
    assert phases.count("combined_who_drill") == 1  # remainder folded into one
    total_drills = phases.count("who_drill") + phases.count("combined_who_drill")
    assert total_drills == MAX_WHO_DRILLS  # never one-per-selection (12)
    assert phases[-1] == "terminal"


def test_build_drill_specs_below_cap_gives_one_drill_each() -> None:
    selections = [SubnicheSelection("sport", f"n{i}") for i in range(MAX_WHO_DRILLS)]
    specs = build_drill_specs(selections)
    assert len(specs) == MAX_WHO_DRILLS
    assert all(isinstance(spec, WhoDrill) for spec in specs)


def test_build_drill_specs_above_cap_folds_the_tail() -> None:
    selections = [
        SubnicheSelection("sport", f"n{i}") for i in range(MAX_WHO_DRILLS + 5)
    ]
    specs = build_drill_specs(selections)
    assert len(specs) == MAX_WHO_DRILLS  # capped
    combined = specs[-1]
    assert isinstance(combined, CombinedWhoDrill)
    # the 5 over the cap + the one displaced by the combined slot
    assert len(combined.selections) == 6


# ── AC #3: free text during a chip turn — both taps AND typed text captured ──


def test_subniche_turn_captures_both_taps_and_typed_custom_interest() -> None:
    """A sub-niche turn with taps AND a typed custom → both become selections + WHO drills.

    WHY: the composer is live during chip turns; a typed custom interest must not be lost
    just because the user also tapped a chip.
    """
    phases, plan = _play(
        ["Sport"],
        lambda root: (["Cricket"], "Formula 1"),
    )
    assert [(s.root_slug, s.label) for s in plan.selections] == [  # type: ignore[attr-defined]
        ("sport", "Cricket"),
        ("sport", "Formula 1"),
    ]
    assert phases.count("who_drill") == 2  # both the tap and the typed pick get a drill


# ── AC #4: engine-owned deterministic skip fast-forward ──


def test_skipping_first_subniche_of_a_category_offers_category_skip() -> None:
    """Skip the first follow-up of a category → the engine offers to skip that category."""
    state = [
        _ex("What news?", tapped=["Sport", "Tech"]),
        _ex("Which sport?", tapped=[]),  # skipped the first follow-up of Sport
    ]
    plan = plan_interest_turn(state)
    assert plan.next_phase == "category_skip_offer"
    assert plan.active_root == "sport"


def test_two_consecutive_category_skips_offer_just_build_my_feed() -> None:
    """Skip two categories in a row → the engine offers 'just build my feed' (deterministic)."""
    state = [
        _ex("What news?", tapped=["Sport", "Tech", "Business"]),
        _ex("Which sport?", tapped=[]),  # skip Sport sub-niches
        _ex(
            "category offer", tapped=[CATEGORY_SKIP_ACCEPT_LABEL]
        ),  # skip Sport category
        _ex("Which tech?", tapped=[]),  # skip Tech sub-niches
        _ex(
            "category offer", tapped=[CATEGORY_SKIP_ACCEPT_LABEL]
        ),  # skip Tech category
    ]
    plan = plan_interest_turn(state)
    assert plan.next_phase == "build_feed_offer"


def test_accepting_build_my_feed_terminates_immediately() -> None:
    """Accepting 'just build my feed' terminates with whatever was gathered so far."""
    _phases, plan = _play(
        ["Sport", "Tech", "Business"],
        lambda root: ([], None),  # skip every sub-niche turn
        category="skip",
        build="accept",
    )
    assert plan.next_phase == "terminal"  # type: ignore[attr-defined]


def test_declining_category_skip_reasks_the_same_categorys_subniches() -> None:
    """'Show me options' on a category offer re-asks that category's sub-niche turn."""
    state = [
        _ex("What news?", tapped=["Sport", "Tech"]),
        _ex("Which sport?", tapped=[]),  # skipped
        _ex("category offer", tapped=[CATEGORY_SKIP_DECLINE_LABEL]),  # show me options
    ]
    plan = plan_interest_turn(state)
    assert plan.next_phase == "subniche"
    assert plan.active_root == "sport"  # same category, re-asked


# ── AC #6: every skipped question recorded as deferred ──


def test_skipped_subniche_and_category_are_recorded_as_deferred() -> None:
    """A skipped sub-niche turn + accepted category skip both land in the deferred list."""
    _phases, plan = _play(
        ["Sport", "Business"],
        # Skip Sport entirely (subniche skip → category skip), engage Business.
        lambda root: (["Startups"], None) if root == "business" else ([], None),
        category="skip",
    )
    kinds = [d.deferral_kind for d in plan.deferred]  # type: ignore[attr-defined]
    assert "subniche_skip" in kinds
    assert "category_skip" in kinds


def test_skipped_who_drill_is_recorded_as_deferred() -> None:
    """A WHO drill answered with 'Nothing specific →' is recorded as a deferred question."""
    _phases, plan = _play(
        ["Sport"],
        lambda root: (["Cricket"], None),
        who="",  # skip the WHO drill
    )
    kinds = [d.deferral_kind for d in plan.deferred]  # type: ignore[attr-defined]
    assert kinds == ["who_drill_skip"]
    assert plan.deferred[0].subniche_label == "Cricket"  # type: ignore[attr-defined]


# ── B3.5: no-mock integration through the real skip-state-machine chain ──


def test_full_skip_fast_forward_chain_is_deterministic_end_to_end() -> None:
    """Drive the real machine: skip 2 categories → build-feed offer → accept → terminal.

    No LLM anywhere in this path — the whole skip fast-forward is engine-owned, so the phase
    sequence is fully determined by the taps.
    """
    phases, plan = _play(
        ["Sport", "Tech", "Business"],
        lambda root: ([], None),  # skip every category
        category="skip",
        build="accept",
    )
    assert phases == [
        "subniche",  # Sport sub-niches asked
        "category_skip_offer",  # ... skipped → offer
        "subniche",  # Tech sub-niches asked
        "category_skip_offer",  # ... skipped → offer
        "build_feed_offer",  # two in a row → build-feed offer
        "terminal",  # accepted
    ]
    assert [d.deferral_kind for d in plan.deferred] == [  # type: ignore[attr-defined]
        "subniche_skip",
        "category_skip",
        "subniche_skip",
        "category_skip",
    ]


# ── Engine seam: deterministic turn shapes (no LLM) + terminal deferred passthrough ──


class _ScriptedLLM:
    """Fake ``LLMClient`` returning sub-niche chips for asks and interests on terminate.

    Reads the CONTROL block's ``must_terminate_now`` flag to decide which action to return,
    and mints unique chip labels per call so successive sub-niche turns never dedupe to
    empty. Records ``calls`` so tests can assert the no-LLM deterministic paths.
    """

    def __init__(self, terminal_interests: list[MicroInterestDraft]) -> None:
        self._terminal_interests = terminal_interests
        self.calls = 0

    async def call_gemini_json(
        self, prompt: str, response_schema: object, system: str = "", model: str = "m"
    ) -> GeminiJsonResult:
        self.calls += 1
        if "must_terminate_now: true" in prompt:
            decision = InterviewDecision(
                action="terminate", micro_interests=self._terminal_interests
            )
        else:
            decision = InterviewDecision(
                action="ask",
                question_text="Which ones pull you in?",
                bubbles=[f"pick{self.calls}-a", f"pick{self.calls}-b"],
            )
        return GeminiJsonResult(
            parsed=decision,
            prompt_tokens=1,
            output_tokens=2,
            total_tokens=3,
            elapsed_ms=1,
            model=model,
        )


@pytest.mark.asyncio
async def test_who_drill_turn_is_engine_worded_with_nothing_specific_and_no_llm() -> (
    None
):
    """A WHO drill turn renders 'Nothing specific →' + type-your-own and costs NO Gemini call."""
    fake = _ScriptedLLM(terminal_interests=[])
    state = [
        _ex("What news?", tapped=["Sport"]),
        _ex(
            "Which sport?", tapped=["Cricket"]
        ),  # sub-niche selected → next is a WHO drill
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert fake.calls == 0  # WHO drills are engine-worded, not model-worded
    assert response.response_kind == "question"
    labels = [b.bubble_label for b in response.bubbles]
    assert WHO_DRILL_NOTHING_LABEL in labels
    assert TYPE_YOUR_OWN_BUBBLE_LABEL in labels


@pytest.mark.asyncio
async def test_category_skip_offer_turn_is_deterministic_and_no_llm() -> None:
    """The category-skip offer carries the fixed control labels and costs no Gemini call."""
    fake = _ScriptedLLM(terminal_interests=[])
    state = [
        _ex("What news?", tapped=["Sport", "Tech"]),
        _ex("Which sport?", tapped=[]),  # skipped → category offer next
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert fake.calls == 0
    labels = [b.bubble_label for b in response.bubbles]
    assert CATEGORY_SKIP_ACCEPT_LABEL in labels
    assert CATEGORY_SKIP_DECLINE_LABEL in labels


@pytest.mark.asyncio
async def test_terminal_response_carries_deferred_questions() -> None:
    """The terminal payload records every skipped question (AC #6) at the engine seam."""
    fake = _ScriptedLLM(
        terminal_interests=[
            MicroInterestDraft(
                display_label="Startups",
                canonical_slug="business.startups",
                search_anchor_terms=["startups", "venture capital"],
            )
        ]
    )
    # Skip Sport (subniche → category skip), engage Business, answer its WHO drill.
    state = [
        _ex("What news?", tapped=["Sport", "Business"]),
        _ex("Which sport?", tapped=[]),  # subniche skip
        _ex("category offer", tapped=[CATEGORY_SKIP_ACCEPT_LABEL]),  # category skip
        _ex("Which business?", tapped=["Startups"]),  # engage Business
        _ex("Startups — name one?", typed="seed rounds"),  # WHO drill answered
        _ex("How do you read Business?", tapped=["Analysis & context"]),  # ANGLE TUNE
        _ex("Anything to mute?", tapped=[]),  # SKIP TUNE (skipped) → terminal next
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "terminal"
    kinds = [d.deferral_kind for d in response.deferred_questions]
    assert "subniche_skip" in kinds
    assert "category_skip" in kinds


@pytest.mark.asyncio
async def test_terminal_phase_llm_failure_returns_typed_retry() -> None:
    """A Gemini failure on the terminal extraction turn is a typed retry, never a dead-end."""

    class _FailingLLM:
        async def call_gemini_json(self, *args: object, **kwargs: object) -> object:
            raise TimeoutError("gemini timed out")

    state = [
        _ex("What news?", tapped=["Sport"]),
        _ex("Which sport?", tapped=["Cricket"]),
        _ex("Cricket — name one?", typed="IPL"),  # WHO drill answered
        _ex("How do you read Sport?", tapped=["Analysis & context"]),  # ANGLE TUNE
        _ex("Anything to mute?", tapped=[]),  # SKIP TUNE → terminal phase, which calls Gemini
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), _FailingLLM()
    )  # type: ignore[arg-type]

    assert response.response_kind == "retry"
    assert response.error_code == "interview_turn_unavailable"
