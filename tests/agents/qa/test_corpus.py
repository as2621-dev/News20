"""Tests for ``load_grounding_corpus`` (Phase 2b SP1).

These tests encode WHY the corpus loader matters (Rule 9), not just what it
returns:
  - **Chunk ordering** — the Detail body must read top-to-bottom; an answer that
    cites ``detail_chunk:0`` must mean the first paragraph. The loader must
    re-order by ``chunk_index``, so the fixture seeds chunks out of order and the
    test fails if insertion order ever leaks through.
  - **Per-story scope (the trust-critical invariant)** — the corpus must contain
    ONLY the requested story's text. The fixture seeds an ``s2`` decoy in every
    table; the test fails if any decoy passage/citation leaks into ``s1``'s
    corpus (a leak would let the model ground an answer on another story).
  - **Citation manifest shape** — chips trace to ``story_sources`` outlet + URL.
  - **Validated Pydantic** — returned objects are the typed models, not dicts.
  - **Bounded budget** — an over-budget corpus fails loud (Rule 12), never
    silently truncates.

The Supabase client is mocked at the boundary (``conftest.FakeSupabaseClient``)
— no network, no LLM, no key.
"""

from __future__ import annotations

from typing import Any

import pytest

from agents.qa.corpus import load_grounding_corpus
from agents.qa.models import (
    CitationTarget,
    GroundingCorpus,
    GroundingPassage,
    PassageOriginKind,
)
from agents.shared.exceptions import CorpusBudgetExceededError, GroundingCorpusError

from tests.agents.qa.conftest import FakeSupabaseClient

# ── Happy path ────────────────────────────────────────────────────────────


def test_load_grounding_corpus_returns_validated_models(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """The loader returns a validated GroundingCorpus of typed passages + targets."""
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    assert isinstance(corpus, GroundingCorpus)
    assert corpus.story_id == "s1"
    assert corpus.passages, "expected grounding passages for s1"
    assert all(isinstance(p, GroundingPassage) for p in corpus.passages)
    assert all(isinstance(t, CitationTarget) for t in corpus.citation_targets)


def test_detail_chunks_come_back_in_chunk_index_order(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """detail_chunks must be ordered by chunk_index, not insertion order.

    WHY: the Detail body reads top-to-bottom and an answer citing detail_chunk:0
    must point at the first paragraph. The fixture seeds chunks as 2,0,1 — if the
    loader trusted insertion order this assertion would catch it.
    """
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    detail_passages = [
        p for p in corpus.passages if p.origin_kind is PassageOriginKind.DETAIL_CHUNK
    ]
    assert [p.passage_order_index for p in detail_passages] == [0, 1, 2]
    assert [p.passage_id for p in detail_passages] == [
        "detail_chunk:0",
        "detail_chunk:1",
        "detail_chunk:2",
    ]
    # First passage's text is the chunk_index=0 paragraph, proving real ordering.
    assert detail_passages[0].passage_text.startswith("S1 first paragraph")


def test_citation_manifest_has_outlet_name_and_url_from_story_sources(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """The citation manifest carries story_sources outlet name + article URL."""
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    by_outlet = {t.source_outlet_name: t for t in corpus.citation_targets}
    assert set(by_outlet) == {"Reuters", "CNN"}
    assert by_outlet["Reuters"].source_article_url == "https://reuters.com/world/hormuz"
    assert by_outlet["Reuters"].source_bias_lean == "center"
    # A source with no URL still yields a chip (URL is nullable in M1's persist).
    assert by_outlet["CNN"].source_article_url is None


def test_body_and_digest_passages_attributed_to_primary_outlet(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """Body + digest passages attribute to the primary outlet (first with a URL)."""
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    # Reuters is the primary outlet (it carries the article URL).
    body_and_digest = [
        p
        for p in corpus.passages
        if p.origin_kind
        in (PassageOriginKind.DETAIL_CHUNK, PassageOriginKind.DIGEST_SCRIPT)
    ]
    assert body_and_digest
    assert all(p.source_outlet_name == "Reuters" for p in body_and_digest)
    # Timeline events have no single attributing outlet.
    timeline = [
        p for p in corpus.passages if p.origin_kind is PassageOriginKind.TIMELINE_EVENT
    ]
    assert timeline and all(p.source_outlet_name is None for p in timeline)


def test_timeline_and_digest_passages_are_present_and_ordered(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """Timeline events are index-ordered; the digest is a single joined passage."""
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    timeline = [
        p for p in corpus.passages if p.origin_kind is PassageOriginKind.TIMELINE_EVENT
    ]
    assert [p.passage_order_index for p in timeline] == [0, 1]
    assert timeline[0].passage_text.startswith("08:10:")

    digests = [
        p for p in corpus.passages if p.origin_kind is PassageOriginKind.DIGEST_SCRIPT
    ]
    assert len(digests) == 1
    # The two caption sentences are joined in sentence_index order into one passage.
    assert digests[0].passage_text.startswith("Tensions rose")
    assert "fifth of the world's oil" in digests[0].passage_text


def test_render_context_block_is_passage_id_labeled(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """The rendered block tags each passage with its stable id (SP2 consumes this)."""
    corpus = load_grounding_corpus("s1", fake_supabase_client)
    block = corpus.render_context_block()

    for passage in corpus.passages:
        assert f"[{passage.passage_id}] {passage.passage_text}" in block


def test_corpus_is_bounded_within_budget(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """The assembled corpus reports a bounded char count under the budget."""
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    expected_chars = sum(len(p.passage_text) for p in corpus.passages)
    assert corpus.total_char_count == expected_chars
    assert 0 < corpus.total_char_count <= corpus.char_budget
    assert corpus.approx_token_count == corpus.total_char_count // 4


# ── Per-story scope (the trust-critical invariant, Rule 9) ──────────────────


def test_corpus_contains_only_the_requested_story(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """No other story's text may leak into s1's corpus.

    WHY (Rule 9): if an s2 passage leaked into s1's corpus the model could ground
    an answer about s1 on s2's facts — exactly the cross-story contamination the
    per-story scope exists to prevent. The fixture seeds an s2 decoy in every
    table; this fails the moment one appears.
    """
    corpus = load_grounding_corpus("s1", fake_supabase_client)

    all_text = " ".join(p.passage_text for p in corpus.passages)
    assert "DECOY" not in all_text
    assert "S2" not in all_text
    assert all("DECOY" not in t.source_outlet_name for t in corpus.citation_targets)

    # And the loader only ever read with the s1 story-id filter (boundary proof).
    for read in fake_supabase_client.read_log:
        assert "s2" not in read["eq"].values()
        assert any(value == "s1" for value in read["eq"].values())


# ── Failure: missing story (fail loud, Rule 12) ─────────────────────────────


def test_missing_story_raises_grounding_corpus_error() -> None:
    """A story with no grounding text raises GroundingCorpusError, not an empty corpus."""
    empty_client = FakeSupabaseClient(
        {
            "detail_chunks": [],
            "story_timeline": [],
            "caption_sentences": [],
            "story_sources": [],
        }
    )
    with pytest.raises(GroundingCorpusError) as exc_info:
        load_grounding_corpus("ghost-story", empty_client)
    assert exc_info.value.story_id == "ghost-story"
    assert exc_info.value.fix_suggestion


def test_over_budget_corpus_raises_budget_error(
    fake_supabase_client: FakeSupabaseClient,
) -> None:
    """An assembled corpus over the char budget fails loud (never truncates)."""
    with pytest.raises(CorpusBudgetExceededError) as exc_info:
        # A tiny budget forces the (small but non-empty) s1 corpus over the ceiling.
        load_grounding_corpus("s1", fake_supabase_client, char_budget=10)
    assert exc_info.value.char_budget == 10
    assert exc_info.value.total_char_count > 10


# ── Edge: empty timeline (optional grounding tables degrade gracefully) ─────


def test_empty_timeline_still_loads_from_detail_chunks(
    seed_tables: dict[str, list[dict[str, Any]]],
) -> None:
    """A story with no timeline events still loads a corpus from detail_chunks.

    WHY: timeline / digest are OPTIONAL grounding (s1 in the prototype is mostly
    detail_chunks). A missing optional table must degrade to zero passages of
    that kind, not error — only a TOTAL absence of grounding fails loud.
    """
    seed_tables["story_timeline"] = []
    client = FakeSupabaseClient(seed_tables)

    corpus = load_grounding_corpus("s1", client)

    assert all(
        p.origin_kind is not PassageOriginKind.TIMELINE_EVENT for p in corpus.passages
    )
    # The detail-chunk spine is still present and ordered.
    detail = [
        p for p in corpus.passages if p.origin_kind is PassageOriginKind.DETAIL_CHUNK
    ]
    assert [p.passage_order_index for p in detail] == [0, 1, 2]


def test_no_sources_yields_empty_citation_manifest_but_still_loads(
    seed_tables: dict[str, list[dict[str, Any]]],
) -> None:
    """No story_sources → empty citation manifest, body passages unattributed."""
    seed_tables["story_sources"] = []
    client = FakeSupabaseClient(seed_tables)

    corpus = load_grounding_corpus("s1", client)

    assert corpus.citation_targets == []
    detail = [
        p for p in corpus.passages if p.origin_kind is PassageOriginKind.DETAIL_CHUNK
    ]
    assert detail and all(p.source_outlet_name is None for p in detail)
