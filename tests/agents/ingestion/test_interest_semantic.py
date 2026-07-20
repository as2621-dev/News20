r"""Unit tests for the semantic relevance key (``interest_semantic``) — RC3, slice #51.

The SEMANTIC half of the two-key relevance lock (PRD decision 5). A story may fill
an interest's slot only when BOTH keys pass: the lexical key (``interest_lexical``,
slice #50) AND this embedding-similarity check between the story and its matched
interest, using the already-wired ``gemini-embedding-001`` (via
``agents.pipeline.clustering.embeddings.embed_texts``).

WHY these matter: lexical matching alone admitted the founder's 07-07 / 07-19 RC3
false positives (venison→AI via "Foundation", Hawaii→interest-rates via "Federal",
Indonesia-port→cricket via "India"), and one case — a zoning lawsuit that GENUINELY
contains the phrase "data center" — clears the lexical key on phrase grounds and can
only be closed semantically. Each test encodes a distinct failure mode; a regression
silently reintroduces junk into the slots the user cares most about.

The Gemini embedding client is mocked at the ``embed_texts`` boundary in EVERY test —
no network, no key, no cost (CLAUDE.md §6 mocking mandate; founder zero-credit rule).

    >>> pytest tests/agents/ingestion/test_interest_semantic.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agents.ingestion.interest_lexical import derive_anchor_specs, evaluate_anchor_match
from agents.ingestion.interest_semantic import (
    SEMANTIC_SIMILARITY_THRESHOLD,
    apply_semantic_relevance_key,
)
from agents.ingestion.models import CanonicalStory, InterestNode

_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

_PATCH_TARGET = "agents.ingestion.interest_semantic.embed_texts"


# --------------------------------------------------------------------------- #
# Topic-vector mock: models "same dominant topic → high cosine, different topic
# → ~0 cosine". Each text is classified to ONE topic (first matching rule wins),
# then mapped to a distinct orthonormal basis vector. Because the interest and the
# genuine story share a topic they score ~1.0 (>= threshold); an off-topic story
# scores 0.0 (< threshold). The ORDER matters: litigation/geography markers are
# checked BEFORE the "data center" anchor, so a lawsuit that merely mentions a data
# center classifies as litigation — the whole point of the zoning case (an embedding
# captures a story's dominant topic, not an incidental entity mention).
# --------------------------------------------------------------------------- #
_TOPIC_RULES: list[tuple[str, str]] = [
    # Dominant-topic markers that must beat an incidental "data center" mention.
    ("sue", "litigation"),
    ("lawsuit", "litigation"),
    ("rezoning", "litigation"),
    ("venison", "food"),
    ("food shelter", "food"),
    ("surabaya", "shipping"),
    ("port capacity", "shipping"),
    ("public school", "education"),
    ("school budget", "education"),
    # Genuine topic anchors (interest queries + on-topic stories).
    ("data center buildout", "datacenter"),
    ("hyperscale", "datacenter"),
    ("data center campus", "datacenter"),
    ("foundation models", "ai"),
    ("artificial intelligence", "ai"),
    ("machine learning", "ai"),
    ("openai", "ai"),
    ("federal reserve", "fed"),
    ("interest rate", "fed"),
    ("india cricket", "cricket"),
    ("cricket", "cricket"),
    ("virat", "cricket"),
    ("ipl", "cricket"),
]

_TOPIC_VECTORS: dict[str, list[float]] = {
    "litigation": [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "food": [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    "shipping": [0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0],
    "education": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
    "datacenter": [0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0],
    "ai": [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0],
    "fed": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0],
    "cricket": [
        1.0,
        0.0,
        0.0,
        0.0,
        0.0,
        0.0,
        1.0,
    ],  # distinct from all above (unit-scaled below)
    "unknown": [0.5, 0.5, 0.5, 0.5, 0.5, 0.5, 0.5],
}


def _topic_of(text: str) -> str:
    """Classify one embedding text to its dominant topic (first matching rule)."""
    lowered = text.lower()
    for marker, topic in _TOPIC_RULES:
        if marker in lowered:
            return topic
    return "unknown"


def _unit(vector: list[float]) -> list[float]:
    norm = sum(component * component for component in vector) ** 0.5
    return [component / norm for component in vector] if norm else vector


def _topic_embed_fn(texts: list[str]) -> list[list[float]]:
    """The AsyncMock's return: one L2-normalized topic vector per input text."""
    return [_unit(_TOPIC_VECTORS[_topic_of(text)]) for text in texts]


def _make_embed_mock() -> AsyncMock:
    """An ``embed_texts`` stand-in that classifies each text to a topic vector."""
    return AsyncMock(side_effect=lambda texts, **_kwargs: _topic_embed_fn(texts))


def _story(story_id: str, title: str, matched: list[str]) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title,
        canonical_url=f"https://example.com/{story_id}",
        canonical_normalized_url=f"https://example.com/{story_id}",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="example.com",
        canonical_matched_interest_ids=list(matched),
    )


def _interest(interest_id: str, label: str, query: str) -> InterestNode:
    return InterestNode(
        interest_id=interest_id,
        interest_slug=interest_id,
        interest_label=label,
        interest_search_query=query,
    )


# --------------------------------------------------------------------------- #
# The four named RC3 cases, as a table: the interest, its lexical anchors, the
# false-positive story (lexical verdict), and a genuine on-topic story. Each row is
# used by BOTH the combined-lock rejection test and the inverse "still admits" test.
# --------------------------------------------------------------------------- #
_RC3_CASES = [
    pytest.param(
        "foundation models, artificial intelligence",  # interest query
        "Rotary club donates venison to the county food shelter",  # false positive
        "OpenAI ships a new foundation models release for reasoning",  # genuine
        id="venison_foundation_to_ai",
    ),
    pytest.param(
        "federal reserve, interest rates, fed",
        "Federal funding boosts Hawaii public school budgets",
        "Federal Reserve holds interest rates steady this month",
        id="hawaii_federal_to_fed",
    ),
    pytest.param(
        "india cricket, bcci, ipl",
        "Indonesia expands Surabaya port capacity for regional trade",
        "Virat stars as India cricket team clinches the series",
        id="indonesia_india_to_cricket",
    ),
    pytest.param(
        "data center buildout, hyperscale campus",
        "Residents sue over rezoning for a new data center",
        "Microsoft begins a hyperscale campus data center buildout in Ohio",
        id="zoning_data_center_to_buildout",
    ),
]


async def _surviving_after_semantic(query: str, title: str) -> list[str]:
    """Run the semantic gate on a single (story matched to `query`'s interest) pair."""
    interest = _interest("int-x", "Interest", query)
    story = _story("s1", title, matched=["int-x"])
    with patch(_PATCH_TARGET, _make_embed_mock()):
        await apply_semantic_relevance_key(
            [story], {"int-x": interest}, llm_client=object()
        )
    return story.canonical_matched_interest_ids


class TestCombinedLockRejectsRc3:
    """All four RC3 false positives are rejected through the combined lexical∧semantic lock."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query, false_positive, _genuine", _RC3_CASES)
    async def test_false_positive_rejected_end_to_end(
        self, query: str, false_positive: str, _genuine: str
    ) -> None:
        """WHY: the acceptance contract — each named RC3 story must NOT fill the interest
        slot. Admission = lexical AND semantic. Three cases fail lexical (short-circuit,
        never reach semantic); the zoning case passes lexical on the genuine 'data center'
        phrase and is closed HERE by semantic. We assert the COMBINED verdict is reject."""
        specs = derive_anchor_specs(query).specs
        title_hay = false_positive.lower()
        lexical_admits = evaluate_anchor_match(title_hay, title_hay, specs)

        semantic_admits = "int-x" in await _surviving_after_semantic(
            query, false_positive
        )

        combined_admits = lexical_admits and semantic_admits
        assert combined_admits is False


class TestInverseGenuineStoryStillPasses:
    """The gate discriminates — a genuine story for each interest still fills the slot."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("query, _false_positive, genuine", _RC3_CASES)
    async def test_genuine_story_passes_both_keys(
        self, query: str, _false_positive: str, genuine: str
    ) -> None:
        """WHY: rejection must not degrade into 'reject everything'. A story genuinely
        about the interest clears lexical (phrase hit) AND semantic (same topic), so it
        keeps the matched interest and fills the slot."""
        specs = derive_anchor_specs(query).specs
        title_hay = genuine.lower()
        assert evaluate_anchor_match(title_hay, title_hay, specs) is True

        assert "int-x" in await _surviving_after_semantic(query, genuine)


class TestGateMechanics:
    """The filter's core behaviour: mutate matched ids, keep passers, drop failers."""

    @pytest.mark.asyncio
    async def test_drops_only_the_dissimilar_matched_interest(self) -> None:
        """WHY: the gate is per-(story, interest) PAIR, not per-story — a story can
        legitimately match one interest and bogusly match another. The bogus one drops;
        the genuine one stays."""
        ai = _interest("ai", "AI", "artificial intelligence")
        food = _interest("food", "Food banks", "food shelter donations")
        # A genuine AI story that a stray query ALSO stamped 'food'.
        story = _story(
            "s1", "OpenAI advances artificial intelligence research", ["ai", "food"]
        )
        with patch(_PATCH_TARGET, _make_embed_mock()):
            stats = await apply_semantic_relevance_key(
                [story], {"ai": ai, "food": food}, llm_client=object()
            )
        assert story.canonical_matched_interest_ids == ["ai"]
        assert stats.pairs_evaluated == 2
        assert stats.pairs_rejected == 1

    @pytest.mark.asyncio
    async def test_story_with_no_matched_interests_costs_nothing(self) -> None:
        """WHY: only lexically-matched pairs cost embeddings — a story with an empty
        matched set (theme-only / source-origin) must be skipped, zero API calls."""
        story = _story("s1", "Some headline", matched=[])
        embed_mock = _make_embed_mock()
        with patch(_PATCH_TARGET, embed_mock):
            stats = await apply_semantic_relevance_key([story], {}, llm_client=object())
        embed_mock.assert_not_called()
        assert stats.embed_call_count == 0
        assert stats.pairs_evaluated == 0

    @pytest.mark.asyncio
    async def test_unknown_interest_id_is_kept_not_dropped(self) -> None:
        """WHY: never fail open, but also never drop on MISSING signal — a matched id
        absent from the taxonomy has no interest text to embed, so it falls back to the
        lexical verdict (kept), rather than being silently dropped."""
        ai = _interest("ai", "AI", "artificial intelligence")
        story = _story("s1", "Rotary club donates venison", ["ai", "ghost-id"])
        with patch(_PATCH_TARGET, _make_embed_mock()):
            await apply_semantic_relevance_key([story], {"ai": ai}, llm_client=object())
        # 'ai' drops (venison vs AI is dissimilar); the unknown id is kept (no signal).
        assert story.canonical_matched_interest_ids == ["ghost-id"]


class TestCostInstrumentation:
    """Per-batch embedding cost is counted for the M2 audit (acceptance criterion 5)."""

    @pytest.mark.asyncio
    async def test_embeds_each_unique_text_once_in_one_batched_call(self) -> None:
        """WHY: credit frugality — the batch embeds each unique story AND each unique
        interest ONCE (deduped), in a SINGLE embed_texts invocation, never per-pair. Two
        stories sharing one interest cost 2 story + 1 interest = 3 embeddings, one call."""
        ai = _interest("ai", "AI", "artificial intelligence")
        story_a = _story("s1", "OpenAI advances artificial intelligence", ["ai"])
        story_b = _story("s2", "New machine learning benchmark released", ["ai"])
        embed_mock = _make_embed_mock()
        with patch(_PATCH_TARGET, embed_mock):
            stats = await apply_semantic_relevance_key(
                [story_a, story_b], {"ai": ai}, llm_client=object()
            )
        # ONE embed_texts call carrying exactly the 3 distinct texts (2 stories + 1 interest).
        assert embed_mock.call_count == 1
        embedded_texts = embed_mock.call_args.args[0]
        assert len(embedded_texts) == 3
        assert stats.stories_checked == 2
        assert stats.interests_checked == 1
        assert stats.embed_call_count == 1
        assert stats.embed_char_count > 0
        assert stats.fell_back_to_lexical is False

    @pytest.mark.asyncio
    async def test_duplicate_interest_across_stories_embedded_once(self) -> None:
        """WHY: never re-embed the same interest text — two stories matched to the SAME
        interest embed that interest exactly once (dedup by interest id)."""
        ai = _interest("ai", "AI", "artificial intelligence")
        stories = [
            _story("s1", "OpenAI advances artificial intelligence", ["ai"]),
            _story("s2", "Anthropic advances artificial intelligence", ["ai"]),
        ]
        embed_mock = _make_embed_mock()
        with patch(_PATCH_TARGET, embed_mock):
            await apply_semantic_relevance_key(stories, {"ai": ai}, llm_client=object())
        embedded_texts = embed_mock.call_args.args[0]
        # exactly one text contains the interest query 'artificial intelligence' AND
        # is NOT one of the two story titles.
        interest_texts = [
            t
            for t in embedded_texts
            if "AI" == t
            or "artificial intelligence" in t.lower()
            and "advances" not in t.lower()
        ]
        assert len(interest_texts) == 1


class TestFailurePosture:
    """Embedding failure mid-batch → strict-lexical fallback + loud log, never fail open."""

    @pytest.mark.asyncio
    async def test_embed_failure_falls_back_to_strict_lexical(self) -> None:
        """WHY THE CONTRACT: if the embedding API fails, admission MUST fall back to the
        lexical verdict — matched ids are left INTACT (the lexical key already gated them),
        never dropped and NEVER widened. This can never be more permissive than lexical."""
        ai = _interest("ai", "AI", "artificial intelligence")
        story = _story("s1", "Rotary club donates venison", ["ai"])
        failing = AsyncMock(side_effect=RuntimeError("gemini 429"))
        with patch(_PATCH_TARGET, failing):
            stats = await apply_semantic_relevance_key(
                [story], {"ai": ai}, llm_client=object()
            )
        # Untouched — the lexically-admitted set survives (strict lexical), not fail-open.
        assert story.canonical_matched_interest_ids == ["ai"]
        assert stats.fell_back_to_lexical is True

    @pytest.mark.asyncio
    async def test_embed_failure_logs_loud_with_fix_suggestion(self) -> None:
        """WHY: a silent degrade to lexical-only hides that the paid semantic tightening
        was skipped (Rule 12). The fallback MUST emit an error log with a fix_suggestion."""
        from structlog.testing import capture_logs

        ai = _interest("ai", "AI", "artificial intelligence")
        story = _story("s1", "Rotary club donates venison", ["ai"])
        failing = AsyncMock(side_effect=RuntimeError("gemini 429"))
        with patch(_PATCH_TARGET, failing), capture_logs() as logs:
            await apply_semantic_relevance_key([story], {"ai": ai}, llm_client=object())
        error_events = [
            entry
            for entry in logs
            if entry.get("log_level") == "error" and "fix_suggestion" in entry
        ]
        assert error_events, (
            "embed failure must log at error level with a fix_suggestion"
        )


class TestThresholdIsSaneConstant:
    """The relevance threshold is a topical floor, not the near-dup same-event bar."""

    def test_threshold_is_a_topical_relevance_floor(self) -> None:
        """WHY: story-vs-interest relatedness is looser than same-event near-duplication
        (clustering's tau_assign 0.75). A relevance floor set that high would reject
        genuine matches; it lives in (0, 0.75)."""
        assert 0.0 < SEMANTIC_SIMILARITY_THRESHOLD < 0.75
