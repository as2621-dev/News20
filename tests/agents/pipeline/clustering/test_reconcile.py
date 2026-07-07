"""Unit tests for semantic story-id reconciliation (FSR-M3 live wiring).

These lock the BUG this module exists to fix: two candidates about one real-world event
that ingestion's URL+0.85-title dedup left as SEPARATE ``story_id`` s (the evidenced
"Egypt beats Australia on penalties" pair — two reworded headlines, two ids, two reels)
must collapse onto ONE shared id BEFORE the produce gate, so only one reel is ever made.

``embed_texts`` is MOCKED (patched at the name imported into ``online_clusterer``) to
return deterministic unit vectors keyed by headline — NO real Gemini, NO network, NO cost
(CLAUDE.md §6). The supabase client is a recording stub whose reads return ``[]`` (empty
cross-day window) and whose writes are no-ops. Each test encodes WHY it matters (Rule 9):

    (a) THE BUG + the cross-category twist: two same-event headlines tagged into DIFFERENT
        categories (``geopolitics`` vs ``sport`` — the football-as-geopolitics mis-tag)
        still MERGE, because the live path drops category blocking (cosine is the true
        gate). An unrelated third story stays separate. Proves the dedup + the
        block_by_category=False decision in one shot.
    (b) tags of the two merged stories are remapped onto the shared id and de-duplicated
        to the LOWEST match depth — else the collapsed story would carry stale ids /
        duplicate interest edges.
    (c) cross-day continuity: a member URL that already aliases to an existing story id
        makes the shared id REUSE that id (produce-once holds across days).
    (d) empty input is a no-op passthrough (never embeds, never writes).

    >>> pytest tests/agents/pipeline/clustering/test_reconcile.py -q
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from agents.ingestion.dedup import normalize_url
from agents.ingestion.models import CanonicalStory, InterestNode, StoryInterestTag
from agents.pipeline.clustering.reconcile import reconcile_story_ids_via_clustering

_NOW = datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc)

# The evidenced pair (prod, ash@gmail.com feed pos 7 & 9): one event, two reworded
# headlines → two ids out of ingestion. Distinct enough (few shared 4-gram shingles) that
# the near-dup prefilter keeps them separate, so ONLY the embedding decides the merge.
_TITLE_EGYPT_A = "Egypt wins first World Cup knockout match beating Australia on penalties"
_TITLE_EGYPT_B = "Egypt defeats Australia in a penalty shootout to reach the World Cup last 16"
_TITLE_UNRELATED = "Central bank holds interest rates steady as inflation cools further"

_EGYPT_INTEREST_ID = "int-football"
_MARKETS_INTEREST_ID = "int-markets"

_INTEREST_NODES = {
    # Same event, but the two candidates were tagged onto DIFFERENT roots — the
    # football-as-geopolitics mis-tag vs a correct sport tag. Cross-category by design.
    _EGYPT_INTEREST_ID: InterestNode(
        interest_id=_EGYPT_INTEREST_ID, interest_slug="sport", interest_label="Sport"
    ),
    _MARKETS_INTEREST_ID: InterestNode(
        interest_id=_MARKETS_INTEREST_ID, interest_slug="markets", interest_label="Markets"
    ),
}


def _l2(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(component * component for component in vector))
    return [component / norm for component in vector]


def _unit_vector_for(text: str) -> list[float]:
    """Deterministic 3-d unit vector keyed by the headline text.

    The two Egypt headlines map to the SAME direction (cos = 1 >> tau → they join); the
    unrelated markets story is orthogonal (cos = 0 → it spawns its own cluster).
    ``text`` is the ``_embedding_text`` (headline, here with no body), so we key on the
    known title prefix.
    """
    if text.startswith("Egypt"):
        return [1.0, 0.0, 0.0]
    if text.startswith("Central bank"):
        return [0.0, 1.0, 0.0]
    raise AssertionError(f"unexpected embedding text: {text!r}")


def _patched_embed() -> AsyncMock:
    async def _fake_embed(texts: list[str], *, llm_client) -> list[list[float]]:  # noqa: ANN001
        return [_unit_vector_for(text) for text in texts]

    return AsyncMock(side_effect=_fake_embed)


def _story(story_id: str, title: str, *, outlet: str, url: str) -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id=story_id,
        canonical_title=title,
        canonical_url=url,
        canonical_normalized_url=normalize_url(url),
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain=outlet,
        covering_outlets=[outlet],
        story_outlet_count=1,
    )


class _RecordingQuery:
    """Chainable Supabase query stub: reads return ``[]``; writes are recorded no-ops."""

    def __init__(self, calls: list[tuple]) -> None:
        self._calls = calls

    def select(self, *args, **_kwargs):
        return self

    def gte(self, *args, **_kwargs):
        return self

    def eq(self, *args, **_kwargs):
        return self

    def upsert(self, payload, *args, **_kwargs):
        self._calls.append(("upsert", payload))
        return self

    def execute(self):
        return type("_Resp", (), {"data": []})()


class _RecordingClient:
    def __init__(self) -> None:
        self.calls_by_table: dict[str, list[tuple]] = {}

    def table(self, name: str):
        return _RecordingQuery(self.calls_by_table.setdefault(name, []))


def _mint_cluster_counter():
    state = {"count": 0}

    def _mint() -> str:
        state["count"] += 1
        return f"clu-{state['count']}"

    return _mint


@pytest.mark.asyncio
async def test_same_event_cross_category_candidates_collapse_to_one_story_id():
    """(a) THE BUG: two reworded same-event headlines — tagged into DIFFERENT categories —
    collapse onto ONE shared story id, while an unrelated story stays separate.

    If this regresses, ingestion's two ids survive to feed assembly and the user sees the
    same event twice (prod pos 7 & 9). The cross-category merge proves the live path's
    category-blocking is OFF (cosine, not the mis-tag, decides same-event)."""
    stories = [
        _story("cand-egypt-a", _TITLE_EGYPT_A, outlet="bbc.com", url="https://bbc.com/egypt-win"),
        _story("cand-egypt-b", _TITLE_EGYPT_B, outlet="reuters.com", url="https://reuters.com/egypt-australia"),
        _story("cand-rates", _TITLE_UNRELATED, outlet="ft.com", url="https://ft.com/rates-hold"),
    ]
    tags = [
        StoryInterestTag(story_interest_story_id="cand-egypt-a", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=0),
        StoryInterestTag(story_interest_story_id="cand-egypt-b", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=0),
        StoryInterestTag(story_interest_story_id="cand-rates", story_interest_interest_id=_MARKETS_INTEREST_ID, story_interest_match_depth=0),
    ]
    client = _RecordingClient()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed()):
        result = await reconcile_story_ids_via_clustering(
            stories,
            tags,
            supabase_client=client,
            llm_client=None,
            interest_nodes=_INTEREST_NODES,
            resolve_existing_story_ids=lambda urls: {},  # all brand-new events
            now_utc=_NOW,
            mint_cluster_id=_mint_cluster_counter(),
        )

    # Two Egypt candidates → one story; the unrelated story is untouched → 2 total.
    assert len(result.reconciled_stories) == 2
    by_id = {s.canonical_story_id: s for s in result.reconciled_stories}
    # Shared id is one of the two originals (the representative, deterministic).
    assert "cand-rates" in by_id
    egypt = next(s for s in result.reconciled_stories if s.canonical_story_id != "cand-rates")
    assert egypt.canonical_story_id in {"cand-egypt-a", "cand-egypt-b"}
    # The merged story unions both outlets — coverage/trust reflects the true event.
    assert set(egypt.covering_outlets) == {"bbc.com", "reuters.com"}
    assert egypt.story_outlet_count == 2


@pytest.mark.asyncio
async def test_tags_are_remapped_onto_shared_id_and_deduped_to_lowest_depth():
    """(b) The two merged stories' interest tags remap onto the shared id and collapse to
    the LOWEST match depth — a stale old id or a duplicated edge would corrupt scoring."""
    stories = [
        _story("cand-egypt-a", _TITLE_EGYPT_A, outlet="bbc.com", url="https://bbc.com/egypt-win"),
        _story("cand-egypt-b", _TITLE_EGYPT_B, outlet="reuters.com", url="https://reuters.com/egypt-australia"),
    ]
    # Both stories tagged on the SAME interest, at different depths (0 leaf, 2 grandparent).
    tags = [
        StoryInterestTag(story_interest_story_id="cand-egypt-a", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=2),
        StoryInterestTag(story_interest_story_id="cand-egypt-b", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=0),
    ]
    client = _RecordingClient()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed()):
        result = await reconcile_story_ids_via_clustering(
            stories, tags,
            supabase_client=client, llm_client=None, interest_nodes=_INTEREST_NODES,
            resolve_existing_story_ids=lambda urls: {}, now_utc=_NOW,
            mint_cluster_id=_mint_cluster_counter(),
        )

    shared_id = result.reconciled_stories[0].canonical_story_id
    # Exactly ONE (story, interest) edge survives, pointing at the shared id, depth 0.
    assert len(result.reconciled_tags) == 1
    tag = result.reconciled_tags[0]
    assert tag.story_interest_story_id == shared_id
    assert tag.story_interest_interest_id == _EGYPT_INTEREST_ID
    assert tag.story_interest_match_depth == 0
    # The importance map is keyed by the shared story id (feeds the assembler's term).
    assert set(result.cluster_importance_by_story) == {shared_id}


@pytest.mark.asyncio
async def test_cross_day_continuity_reuses_existing_story_id():
    """(c) A member URL that already aliases to a prior-day story id makes the shared id
    REUSE that id — so a multi-day event keeps one id and produce-once holds across days."""
    stories = [
        _story("cand-egypt-a", _TITLE_EGYPT_A, outlet="bbc.com", url="https://bbc.com/egypt-win"),
        _story("cand-egypt-b", _TITLE_EGYPT_B, outlet="reuters.com", url="https://reuters.com/egypt-australia"),
    ]
    tags = [
        StoryInterestTag(story_interest_story_id="cand-egypt-a", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=0),
    ]
    aliased_url = normalize_url("https://reuters.com/egypt-australia")
    client = _RecordingClient()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed()):
        result = await reconcile_story_ids_via_clustering(
            stories, tags,
            supabase_client=client, llm_client=None, interest_nodes=_INTEREST_NODES,
            resolve_existing_story_ids=lambda urls: {aliased_url: "story-yesterday"},
            now_utc=_NOW, mint_cluster_id=_mint_cluster_counter(),
        )

    assert len(result.reconciled_stories) == 1
    assert result.reconciled_stories[0].canonical_story_id == "story-yesterday"
    # The tag followed the story onto the reused id.
    assert result.reconciled_tags[0].story_interest_story_id == "story-yesterday"


@pytest.mark.asyncio
async def test_empty_pool_is_a_noop_passthrough():
    """(d) An empty candidate pool returns unchanged inputs and NEVER embeds or writes —
    no paid call is made for a dry night."""
    embed = _patched_embed()
    client = _RecordingClient()
    with patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=embed):
        result = await reconcile_story_ids_via_clustering(
            [], [],
            supabase_client=client, llm_client=None, interest_nodes=_INTEREST_NODES,
            resolve_existing_story_ids=lambda urls: {}, now_utc=_NOW,
        )

    assert result.reconciled_stories == []
    assert result.reconciled_tags == []
    assert result.cluster_importance_by_story == {}
    embed.assert_not_awaited()


# ── Issue #34 — cross-category merge guard (fetching interest wins, #35 doctrine) ──

_GEO_INTEREST_ID = "int-geopolitics"

_GUARD_INTEREST_NODES = {
    **_INTEREST_NODES,
    _GEO_INTEREST_ID: InterestNode(
        interest_id=_GEO_INTEREST_ID,
        interest_slug="geopolitics",
        interest_label="Geopolitics",
    ),
}


@pytest.mark.asyncio
async def test_cross_category_merge_keeps_representative_category_and_logs_conflict():
    """(e) Issue #34: a cross-category merge must NOT flip the surviving story into a
    category contradicting its fetching interest — and the conflict must be logged.

    The representative (egypt-a) was fetched via a sport interest (shifted keyword
    depth 1, the #35 uniform-shift shape); the absorbed member (egypt-b) carries a
    geopolitics tag at depth 0 that would WIN assign_category's lowest-depth rule
    after the tag union — silently flipping a sport story into geopolitics. The
    guard must clamp the foreign tag's depth so the representative's fetching
    category still wins, and emit a structured ``reconcile_category_conflict``."""
    from unittest.mock import MagicMock

    from agents.pipeline.clustering import reconcile as reconcile_module
    from agents.pipeline.stages.ranking import _index_tags_by_story, assign_category

    stories = [
        _story("cand-egypt-a", _TITLE_EGYPT_A, outlet="bbc.com", url="https://bbc.com/egypt-win"),
        _story("cand-egypt-b", _TITLE_EGYPT_B, outlet="reuters.com", url="https://reuters.com/egypt-australia"),
    ]
    tags = [
        StoryInterestTag(story_interest_story_id="cand-egypt-a", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=1),
        StoryInterestTag(story_interest_story_id="cand-egypt-b", story_interest_interest_id=_GEO_INTEREST_ID, story_interest_match_depth=0),
    ]
    client = _RecordingClient()
    fake_logger = MagicMock()
    with (
        patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed()),
        patch.object(reconcile_module, "logger", fake_logger),
    ):
        result = await reconcile_story_ids_via_clustering(
            stories, tags,
            supabase_client=client, llm_client=None, interest_nodes=_GUARD_INTEREST_NODES,
            resolve_existing_story_ids=lambda urls: {}, now_utc=_NOW,
            mint_cluster_id=_mint_cluster_counter(),
        )

    assert len(result.reconciled_stories) == 1
    shared_id = result.reconciled_stories[0].canonical_story_id
    # The merged story still classifies into the REPRESENTATIVE's fetching category.
    merged_category = assign_category(
        shared_id, _index_tags_by_story(result.reconciled_tags), _GUARD_INTEREST_NODES
    )
    assert merged_category == "sport", (
        "the absorbed member's lower-depth geopolitics tag must not flip the "
        "representative sport story's category (issue #34 merge guard)"
    )
    # The conflict is visible: a structured event fired exactly once.
    conflict_calls = [
        call for call in fake_logger.info.call_args_list
        if call.args and call.args[0] == "reconcile_category_conflict"
    ]
    assert len(conflict_calls) == 1
    kwargs = conflict_calls[0].kwargs
    assert kwargs["story_id"] == shared_id
    assert kwargs["representative_category"] == "sport"


@pytest.mark.asyncio
async def test_same_category_merge_does_not_log_conflict_or_touch_depths():
    """(f) Issue #34 boundary: a same-category merge is NOT a conflict — no
    ``reconcile_category_conflict`` event, and tag depths pass through the existing
    lowest-depth dedup untouched (regression pin on test (b)'s contract)."""
    from unittest.mock import MagicMock

    from agents.pipeline.clustering import reconcile as reconcile_module

    stories = [
        _story("cand-egypt-a", _TITLE_EGYPT_A, outlet="bbc.com", url="https://bbc.com/egypt-win"),
        _story("cand-egypt-b", _TITLE_EGYPT_B, outlet="reuters.com", url="https://reuters.com/egypt-australia"),
    ]
    tags = [
        StoryInterestTag(story_interest_story_id="cand-egypt-a", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=2),
        StoryInterestTag(story_interest_story_id="cand-egypt-b", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=0),
    ]
    client = _RecordingClient()
    fake_logger = MagicMock()
    with (
        patch("agents.pipeline.clustering.online_clusterer.embed_texts", new=_patched_embed()),
        patch.object(reconcile_module, "logger", fake_logger),
    ):
        result = await reconcile_story_ids_via_clustering(
            stories, tags,
            supabase_client=client, llm_client=None, interest_nodes=_INTEREST_NODES,
            resolve_existing_story_ids=lambda urls: {}, now_utc=_NOW,
            mint_cluster_id=_mint_cluster_counter(),
        )

    conflict_events = [
        call.args[0] for call in fake_logger.info.call_args_list
        if call.args and call.args[0] == "reconcile_category_conflict"
    ]
    assert conflict_events == []
    # Existing lowest-depth dedup contract holds unchanged.
    assert len(result.reconciled_tags) == 1
    assert result.reconciled_tags[0].story_interest_match_depth == 0
