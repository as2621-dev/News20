"""Behavior tests for the interview turn engine (FSR interview slice #1).

Each test encodes WHY a rule matters (Rule 9): the guardrails exist so onboarding can
never force an interest on a user, never invent one they didn't reach, never dead-end on a
Gemini failure, and always converge to a validated terminal list. Gemini is faked at the
``LLMClient.call_gemini_json`` boundary — no live call.
"""

from __future__ import annotations

import pytest

from agents.interview.constants import (
    SKIP_BUBBLE_LABEL,
    TYPE_YOUR_OWN_BUBBLE_LABEL,
)
from agents.interview.engine import (
    derive_ladder,
    lit_roots_from_state,
    run_interview_turn,
    total_taps,
    validate_and_dedup,
)
from agents.interview.models import (
    InterviewDecision,
    InterviewExchange,
    InterviewTurnRequest,
    MicroInterestDraft,
)
from agents.pipeline.llm_clients import GeminiJsonResult


class _FakeLLMClient:
    """A stand-in for :class:`LLMClient` that returns a canned decision or raises.

    Mocks the exact boundary the engine calls (``call_gemini_json``) so no live Gemini
    request is made. Records whether it was called so tests can assert the no-LLM paths.
    """

    def __init__(
        self, decision: InterviewDecision | None = None, raises: Exception | None = None
    ) -> None:
        self._decision = decision
        self._raises = raises
        self.call_count = 0
        self.last_prompt: str | None = None

    async def call_gemini_json(
        self, prompt: str, response_schema: object, system: str = "", model: str = "m"
    ) -> GeminiJsonResult:
        self.call_count += 1
        self.last_prompt = prompt
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


def _exchange(
    question: str,
    offered: list[str] | None = None,
    tapped: list[str] | None = None,
    typed: str | None = None,
) -> InterviewExchange:
    return InterviewExchange(
        question_text=question,
        bubbles_offered=offered or [],
        bubbles_tapped=tapped or [],
        free_text_entered=typed,
    )


def _valid_draft(**overrides: object) -> MicroInterestDraft:
    base = {
        "display_label": "IPL auctions & transfers",
        "canonical_slug": "sport.cricket.ipl.auctions",
        "search_anchor_terms": ["IPL auction", "player transfers"],
        "strict": False,
    }
    base.update(overrides)
    return MicroInterestDraft(**base)  # type: ignore[arg-type]


# ── Turn 1: the fixed roots, no LLM (acceptance: turn-1 bubbles are 8 fixed roots) ──


@pytest.mark.asyncio
async def test_turn_one_returns_eight_roots_plus_affordances_without_calling_gemini() -> (
    None
):
    """First turn must render the 8 roots + skip + type and cost NO Gemini call.

    WHY: turn 1 is a fixed menu; spending an LLM call on it is waste and a failure risk.
    """
    fake = _FakeLLMClient()
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=[]), fake
    )  # type: ignore[arg-type]

    assert fake.call_count == 0
    assert response.response_kind == "question"
    option_labels = [
        b.bubble_label for b in response.bubbles if b.bubble_kind == "option"
    ]
    assert option_labels == [
        "AI",
        "Geopolitics",
        "Business",
        "Environment",
        "Politics",
        "Tech",
        "Sport",
        "Arts",
    ]
    kinds = [b.bubble_kind for b in response.bubbles]
    assert kinds[-2:] == ["skip", "type_your_own"]


# ── Bubble guardrails: <= 6 options, always skip + type, never re-offer a tapped label ──


@pytest.mark.asyncio
async def test_ask_turn_caps_options_at_six_and_always_appends_skip_and_type() -> None:
    """A deeper ask turn renders <= 6 options and ALWAYS the skip + type affordances.

    WHY: every question must be skippable and typo-able (PRD #5/#6), and a wall of bubbles
    is a UX failure — the server, not the model, guarantees both.
    """
    decision = InterviewDecision(
        action="ask",
        question_text="Which cricket?",
        bubbles=[
            "IPL",
            "Test cricket",
            "T20 World Cup",
            "The Ashes",
            "BBL",
            "PSL",
            "SA20",
            SKIP_BUBBLE_LABEL,
        ],
    )
    fake = _FakeLLMClient(decision=decision)
    state = [_exchange("What news?", tapped=["Sport"])]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    options = [b for b in response.bubbles if b.bubble_kind == "option"]
    assert (
        len(options) == 6
    )  # capped, and the injected skip label was dropped from options
    assert response.bubbles[-2].bubble_kind == "skip"
    assert response.bubbles[-1].bubble_kind == "type_your_own"


@pytest.mark.asyncio
async def test_ask_turn_never_reoffers_an_already_tapped_label() -> None:
    """A previously tapped label is never re-offered on a later sub-niche turn (no loops).

    Two roots are lit, so after the Sport sub-niche turn is answered the NEXT LLM ask is
    the Tech sub-niche turn — and a label the user already tapped must not resurface.
    """
    decision = InterviewDecision(
        action="ask", question_text="More?", bubbles=["IPL", "Test cricket"]
    )
    fake = _FakeLLMClient(decision=decision)
    state = [
        _exchange("What news?", tapped=["Sport", "Tech"]),
        _exchange("Which cricket?", tapped=["IPL"]),  # Sport sub-niche answered
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]
    option_labels = [
        b.bubble_label for b in response.bubbles if b.bubble_kind == "option"
    ]
    assert "IPL" not in option_labels
    assert "Test cricket" in option_labels


# ── Server-owned termination: the phase machine terminates, overriding a model 'ask' ──


@pytest.mark.asyncio
async def test_completed_who_drills_force_terminal_even_when_model_wants_to_ask() -> (
    None
):
    """Once every WHO drill is answered the server terminates, overriding a model 'ask'.

    WHY: the engine — not a chatty model — owns the flow (Rule 5). After the sub-niche and
    its one WHO drill are answered there is nothing left to ask, so a model that still wants
    to ask is overridden into the terminal extraction.
    """
    model_wants_ask = InterviewDecision(
        action="ask", question_text="Even deeper?", bubbles=["More"]
    )
    fake = _FakeLLMClient(decision=model_wants_ask)
    state = [
        _exchange("What news?", tapped=["Sport"]),
        _exchange("Which sport?", tapped=["Cricket"]),  # sub-niche selected
        _exchange("Cricket — name one, or skip.", typed="IPL"),  # WHO drill answered
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "terminal"
    # Model returned no interests, so the engine falls back to the lit root itself.
    assert [i.canonical_slug for i in response.micro_interests] == ["sport"]


# ── Skip-everything degenerate path (acceptance: empty terminal + roots-only signal) ──


@pytest.mark.asyncio
async def test_skip_everything_returns_empty_terminal_with_roots_only_fallback_and_no_llm() -> (
    None
):
    """Skipping the first question yields an empty terminal + roots-only flag, no LLM call.

    WHY: a user who skips must still get a working feed (roots-only baseline), and we must
    not pay for an LLM call to say "nothing".
    """
    fake = _FakeLLMClient()
    state = [_exchange("What news?", tapped=[SKIP_BUBBLE_LABEL])]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert fake.call_count == 0
    assert response.response_kind == "terminal"
    assert response.micro_interests == []
    assert response.roots_only_fallback is True


# ── Gibberish free-text: ask once, then park (never loop, never fabricate a niche) ──


@pytest.mark.asyncio
async def test_two_free_text_only_turns_force_terminal_park_at_root_level() -> None:
    """A second free-text-only turn forces termination; gibberish parks as a ROOT-level slug.

    WHY: unmappable input must never be fabricated into a deep niche (spec §3) — it parks
    at the closest root, and it can't loop forever.
    """
    parked = InterviewDecision(
        action="ask",  # model still wants to ask; server overrides to terminate
        micro_interests=[
            MicroInterestDraft(
                display_label="asdfgh",
                canonical_slug="tech",  # root-level park, not a fabricated niche
                search_anchor_terms=["asdfgh", "asdfgh news"],
            )
        ],
    )
    fake = _FakeLLMClient(decision=parked)
    state = [
        _exchange("What news?", typed="asdfgh"),
        _exchange("Did you mean something specific?", typed="asdfgh"),
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]
    assert response.response_kind == "terminal"
    assert [i.canonical_slug for i in response.micro_interests] == ["tech"]
    assert response.micro_interests[0].display_label == "asdfgh"


@pytest.mark.asyncio
async def test_free_text_does_not_let_model_mint_deep_niche_under_untapped_root() -> (
    None
):
    """Free text widens traceability ONLY to root-level parks — never a deep untraced niche.

    WHY: the acceptance rule "never return an interest the user didn't tap/type toward"
    must hold even when the model proposes an unrelated deep niche under an untapped root.
    """
    decision = InterviewDecision(
        action="terminate",
        micro_interests=[
            _valid_draft(  # sport.cricket.ipl.auctions — but Sport was never tapped
                canonical_slug="ai.agents.autogpt",
                display_label="AutoGPT",
                search_anchor_terms=["AutoGPT", "autonomous agents"],
            )
        ],
    )
    fake = _FakeLLMClient(decision=decision)
    # User tapped nothing and typed unrelated text; the ai.* niche is untraceable.
    state = [_exchange("What news?", typed="the ashes cricket")]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]
    assert response.response_kind == "terminal"
    assert response.micro_interests == []  # dropped, then no lit roots → empty terminal
    assert response.roots_only_fallback is True


# ── Natural terminal: the marquee happy path (acceptance #1) ──


@pytest.mark.asyncio
async def test_natural_terminal_returns_fully_formed_micro_interest() -> None:
    """A drilled conversation terminates into a valid, anchored, laddered micro-interest."""
    decision = InterviewDecision(action="terminate", micro_interests=[_valid_draft()])
    fake = _FakeLLMClient(decision=decision)
    state = [
        _exchange("What news?", tapped=["Sport"]),
        _exchange("Which sport?", tapped=["Cricket"]),
        _exchange("Which cricket?", tapped=["IPL"]),
    ]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "terminal"
    assert len(response.micro_interests) == 1
    interest = response.micro_interests[0]
    assert interest.display_label == "IPL auctions & transfers"
    assert interest.canonical_slug == "sport.cricket.ipl.auctions"
    # Ladder is the PARENT chain, excluding the leaf slug itself (spec §4).
    assert interest.ladder == ["sport", "sport.cricket", "sport.cricket.ipl"]
    assert len(interest.search_anchor_terms) >= 2
    # Cost/latency is tracked and surfaced from the (mocked) Gemini usage.
    assert response.turn_cost.total_tokens == 33


# ── Failure contract: Gemini timeout / malformed JSON → retry body (never a raise) ──


@pytest.mark.asyncio
async def test_gemini_failure_returns_typed_retry_body() -> None:
    """A Gemini timeout / malformed JSON becomes a typed retry response, never an exception."""
    fake = _FakeLLMClient(raises=TimeoutError("gemini timed out"))
    state = [_exchange("What news?", tapped=["Sport"])]
    response = await run_interview_turn(
        InterviewTurnRequest(conversation_state=state), fake
    )  # type: ignore[arg-type]

    assert response.response_kind == "retry"
    assert response.error_code == "interview_turn_unavailable"
    assert response.retry_hint


# ── Pure guardrail functions ──


def test_lit_roots_maps_only_first_turn_taps_to_slugs() -> None:
    state = [
        _exchange("What news?", tapped=["Sport", "AI", SKIP_BUBBLE_LABEL]),
        _exchange("Which sport?", tapped=["Cricket"]),  # not a root — ignored
    ]
    assert lit_roots_from_state(state) == ["sport", "ai"]


def test_total_taps_excludes_meta_affordances() -> None:
    state = [
        _exchange("q", tapped=["Sport", SKIP_BUBBLE_LABEL, TYPE_YOUR_OWN_BUBBLE_LABEL])
    ]
    assert total_taps(state) == 1


def test_derive_ladder_is_cumulative_prefixes() -> None:
    # Parent chain only — the leaf slug is not part of its own ladder (spec §4).
    assert derive_ladder("sport.cricket.ipl") == ["sport", "sport.cricket"]
    assert derive_ladder("sport") == []  # a root has no parents


def test_validate_drops_unanchored_slug() -> None:
    drafts = [_valid_draft(canonical_slug="notaroot.thing")]
    assert validate_and_dedup(drafts, lit_roots=["sport"], had_free_text=False) == []


def test_validate_drops_interest_with_under_two_anchor_terms() -> None:
    drafts = [_valid_draft(search_anchor_terms=["only one"])]
    assert validate_and_dedup(drafts, lit_roots=["sport"], had_free_text=False) == []


def test_validate_drops_untraceable_interest_when_root_not_tapped_and_no_text() -> None:
    """An interest whose root the user never tapped (and never typed) is never minted."""
    drafts = [_valid_draft(canonical_slug="business.markets.ipos")]
    assert validate_and_dedup(drafts, lit_roots=["sport"], had_free_text=False) == []


def test_validate_dedups_same_canonical_slug_reached_twice() -> None:
    drafts = [_valid_draft(), _valid_draft(display_label="cricket auctions")]
    result = validate_and_dedup(drafts, lit_roots=["sport"], had_free_text=False)
    assert len(result) == 1


def test_validate_normalizes_model_underscore_slug_instead_of_dropping() -> None:
    """M4 day-1 finding: Gemini proposes the RIGHT niche with underscore separators
    (``ai.foundation_models``); dropping it parked real personas on roots-only.
    Separator spelling is deterministic variance, so the guard folds it to the
    canonical dash form rather than rejecting the user's actual interest."""
    drafts = [_valid_draft(canonical_slug="ai.foundation_models")]
    result = validate_and_dedup(drafts, lit_roots=["ai"], had_free_text=False)
    assert [item.canonical_slug for item in result] == ["ai.foundation-models"]
    assert result[0].ladder == ["ai"]


def test_validate_still_drops_slug_malformed_after_normalization() -> None:
    drafts = [_valid_draft(canonical_slug="notaroot.foundation_models")]
    assert validate_and_dedup(drafts, lit_roots=["ai"], had_free_text=False) == []


def test_validate_normalizes_mixed_separators_and_edge_dashes() -> None:
    drafts = [_valid_draft(canonical_slug="Sport.Cricket .IPL__Auctions")]
    result = validate_and_dedup(drafts, lit_roots=["sport"], had_free_text=False)
    assert [item.canonical_slug for item in result] == ["sport.cricket.ipl-auctions"]
