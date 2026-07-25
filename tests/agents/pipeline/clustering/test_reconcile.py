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
    assert result.category_override_by_story == {}
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
async def test_cross_category_merge_enforces_representative_category_and_never_touches_tag_depths():
    """(e) Issues #34 + #70: a cross-category merge is a category conflict — it must
    be logged as a structured ``reconcile_category_conflict`` event, PINNED via the
    returned ``category_override_by_story`` map to the UNION resolution (merged tags
    + merged themes through ``assign_category`` — post-#70 the most specific verified
    match wins, NOT the representative's provisional category, which the 2026-07-25
    audit showed froze theme-scrambled verdicts onto whole merges), and the remapped
    tags' ``match_depth`` values must be BYTE-IDENTICAL to their inputs.

    WHY the depths must not move (review-panel HIGH): ``story_interest_match_depth`` is
    also the ranker's DepthMatch input and is persisted verbatim to ``story_interests``
    — clamping it to steer the category contest would cut a genuine follower of the
    foreign interest from DepthMatch 1.0 to 0.6/0.3 (or zero it entirely past depth 2).
    Enforcement therefore rides an explicit override map to the ``assign_category``
    call sites, never a depth mutation (the #34 remainder, panel-agreed design)."""
    from unittest.mock import MagicMock

    from agents.pipeline.clustering import reconcile as reconcile_module

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
    # The ranking-facing depths are UNTOUCHED — remapped onto the shared id verbatim.
    depth_by_interest = {
        tag.story_interest_interest_id: tag.story_interest_match_depth
        for tag in result.reconciled_tags
    }
    assert depth_by_interest == {_EGYPT_INTEREST_ID: 1, _GEO_INTEREST_ID: 0}
    assert all(tag.story_interest_story_id == shared_id for tag in result.reconciled_tags)
    # Persist parity: the exact rows ranking persists verbatim to ``story_interests``
    # carry the SAME depths — the guard steered category via the override map only.
    from agents.pipeline.persist_helpers import build_story_interest_rows

    persisted_rows = build_story_interest_rows(shared_id, result.reconciled_tags)
    assert {
        row["story_interest_interest_id"]: row["story_interest_match_depth"]
        for row in persisted_rows
    } == {_EGYPT_INTEREST_ID: 1, _GEO_INTEREST_ID: 0}
    # ENFORCEMENT (issue #70): the merged story is pinned to the UNION resolution —
    # the absorbed member's DIRECT geopolitics match (depth 0) is more specific than
    # the representative's ancestor tag (depth 1), so the pin is geopolitics, the
    # same verdict every theme-threaded assign_category call site would reach (one
    # rule, no divergent rep special case). Never via a depth clamp.
    assert result.category_override_by_story == {shared_id: "geopolitics"}
    # The conflict is visible: a structured event fired exactly once, naming the pin.
    conflict_calls = [
        call for call in fake_logger.info.call_args_list
        if call.args and call.args[0] == "reconcile_category_conflict"
    ]
    assert len(conflict_calls) == 1
    kwargs = conflict_calls[0].kwargs
    assert kwargs["story_id"] == shared_id
    assert kwargs["pinned_category"] == "geopolitics"
    assert sorted(kwargs["contender_categories"]) == ["geopolitics", "sport"]


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


@pytest.mark.asyncio
async def test_untagged_representative_conflict_pins_union_resolution_and_tags_pass_through():
    """(g) Issues #34 + #70 boundary: when the REPRESENTATIVE has no resolvable tags
    of its own, the pin no longer depends on the representative at all — the UNION
    resolution decides (post-#70 the rep-pin was the arbitrariness: the 2026-07-25
    AFF-Cup merge froze the rep's wrong verdict). Here the two absorbed members tie
    at depth 0 across sport/geopolitics with no theme signal, so the deterministic
    slug tiebreak pins geopolitics; the conflict is logged; every remapped tag depth
    passes through untouched (Rule 9: pin it or it silently changes)."""
    from unittest.mock import MagicMock

    from agents.pipeline.clustering import reconcile as reconcile_module

    stories = [
        # Representative (smallest batch index) — deliberately UNTAGGED.
        _story("cand-egypt-a", _TITLE_EGYPT_A, outlet="bbc.com", url="https://bbc.com/egypt-win"),
        _story("cand-egypt-b", _TITLE_EGYPT_B, outlet="reuters.com", url="https://reuters.com/egypt-australia"),
        _story(
            "cand-egypt-c",
            "Egypt penalty-shootout win over Australia sends fans into the streets",
            outlet="apnews.com",
            url="https://apnews.com/egypt-celebrations",
        ),
    ]
    tags = [
        # Two absorbed members tagged in two DIFFERENT foreign categories.
        StoryInterestTag(story_interest_story_id="cand-egypt-b", story_interest_interest_id=_EGYPT_INTEREST_ID, story_interest_match_depth=0),
        StoryInterestTag(story_interest_story_id="cand-egypt-c", story_interest_interest_id=_GEO_INTEREST_ID, story_interest_match_depth=0),
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

    # All three collapsed onto the untagged representative's id.
    assert len(result.reconciled_stories) == 1
    shared_id = result.reconciled_stories[0].canonical_story_id
    # The conflict fired once, naming the union-resolution pin.
    conflict_calls = [
        call for call in fake_logger.info.call_args_list
        if call.args and call.args[0] == "reconcile_category_conflict"
    ]
    assert len(conflict_calls) == 1
    assert conflict_calls[0].kwargs["story_id"] == shared_id
    assert conflict_calls[0].kwargs["pinned_category"] == "geopolitics"
    # The pin is the union resolution (equal-depth tie, no themes → slug tiebreak) —
    # never an arbitrary representative fallback.
    assert result.category_override_by_story == {shared_id: "geopolitics"}
    # Nothing was clamped: both remapped tags keep depth 0.
    depth_by_interest = {
        tag.story_interest_interest_id: tag.story_interest_match_depth
        for tag in result.reconciled_tags
    }
    assert depth_by_interest == {_EGYPT_INTEREST_ID: 0, _GEO_INTEREST_ID: 0}
    assert all(tag.story_interest_story_id == shared_id for tag in result.reconciled_tags)
