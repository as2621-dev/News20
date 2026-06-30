"""Business-rule tests for the catalog cluster resolver (Phase FSR-M1 SP2).

These encode WHY each rule matters (Rule 9), not merely the shape — the resolver is
the **trust contract** of the source-first thesis (PRD Decision #7): no randoms, no
duplicates, a person shown once. A regression that let a personality's own YouTube
row leak alongside their card, or surfaced an empty cluster, or rendered a followable
twice in a category, would break exactly the promise M1 exists to de-risk.

Pure: fixture rows built inline, NO DB / network / clock — mirrors
``tests/agents/pipeline/test_categories.py``.
"""

from __future__ import annotations

from agents.catalog.cluster_resolver import resolve_category_clusters
from agents.catalog.models import (
    CatalogSourceRow,
    ClusterMemberRef,
    ClusterRow,
    PersonalityRow,
)


# ── fixture builders (inline rows, no DB) ─────────────────────────────────────
def _source(
    source_id: str,
    *,
    content_source_type: str = "youtube_channel",
    external_id: str = "ext",
    source_name: str = "Source",
    is_curated: bool = True,
) -> CatalogSourceRow:
    return CatalogSourceRow(
        source_id=source_id,
        content_source_type=content_source_type,
        external_id=external_id,
        source_name=source_name,
        topic_tags=["ai"],
        popularity_score=50.0,
        is_curated=is_curated,
    )


def _personality(
    personality_id: str,
    *,
    display_name: str = "Person",
    aliases: list[str] | None = None,
    youtube_channel_ids: list[str] | None = None,
    is_curated: bool = True,
) -> PersonalityRow:
    return PersonalityRow(
        personality_id=personality_id,
        display_name=display_name,
        aliases=aliases or [],
        youtube_channel_ids=youtube_channel_ids or [],
        topic_tags=["ai"],
        popularity_score=50.0,
        is_curated=is_curated,
    )


def _cluster(
    cluster_id: str,
    *,
    cluster_slug: str = "slug",
    cluster_category: str = "ai",
    cluster_sort_order: int = 0,
    is_curated: bool = True,
) -> ClusterRow:
    return ClusterRow(
        cluster_id=cluster_id,
        cluster_slug=cluster_slug,
        cluster_label=cluster_slug.title(),
        cluster_category=cluster_category,
        cluster_sort_order=cluster_sort_order,
        is_curated=is_curated,
    )


def _smember(cluster_id: str, source_id: str, order: int) -> ClusterMemberRef:
    return ClusterMemberRef(cluster_id=cluster_id, source_id=source_id, member_sort_order=order)


def _pmember(cluster_id: str, personality_id: str, order: int) -> ClusterMemberRef:
    return ClusterMemberRef(cluster_id=cluster_id, personality_id=personality_id, member_sort_order=order)


# ── (1) cluster membership + order ────────────────────────────────────────────
def test_members_returned_in_member_sort_order() -> None:
    """A 3-member cluster renders its members in member_sort_order.

    WHY: the onboarding grid is an ORDERED bulk-select; the editor's sequence is the
    product signal. Returning members out of order silently reorders the screen.
    """
    sources = [_source("s1"), _source("s2"), _source("s3")]
    clusters = [_cluster("c1", cluster_slug="threes")]
    # Inserted out of order on purpose; resolver must sort by member_sort_order.
    members = [_smember("c1", "s3", 2), _smember("c1", "s1", 0), _smember("c1", "s2", 1)]

    out = resolve_category_clusters("ai", clusters, members, sources, [])

    assert len(out) == 1
    assert [m.followable_id for m in out[0].members] == ["s1", "s2", "s3"]


# ── (2) empty cluster ─────────────────────────────────────────────────────────
def test_cluster_with_only_uncurated_member_is_dropped() -> None:
    """A cluster whose only member is un-curated is NOT in the output.

    WHY: un-curated rows are never rendered; a cluster emptied by that must not
    surface as a header with zero rows (a broken-looking grid section).
    """
    sources = [_source("s1", is_curated=False)]
    clusters = [_cluster("c1", cluster_slug="empty")]
    members = [_smember("c1", "s1", 0)]

    out = resolve_category_clusters("ai", clusters, members, sources, [])

    assert out == []


def test_cluster_with_missing_member_row_is_dropped_not_raised() -> None:
    """A member whose referenced row is missing is skipped (no raise), emptying the cluster.

    WHY: stale member refs (a deleted source) must degrade gracefully, not crash
    onboarding.
    """
    clusters = [_cluster("c1", cluster_slug="dangling")]
    members = [_smember("c1", "ghost", 0)]

    out = resolve_category_clusters("ai", clusters, members, [], [])

    assert out == []


# ── (3) personality dedup (the trust contract) ────────────────────────────────
def test_personality_bundled_handles_are_suppressed_card_shown_once() -> None:
    """A personality bundling a YouTube channel AND an X alias that ALSO exist as
    content_sources rows ⇒ those two source rows are ABSENT everywhere, while the
    personality card appears once.

    WHY: PRD Decision #7 — showing a person both as a card and as their raw handle
    rows breaks the trust contract ("no duplicates / no randoms"). This is the
    load-bearing no-dup rule.
    """
    yt_row = _source("yt1", content_source_type="youtube_channel", external_id="UC_persn", source_name="Persn YT")
    x_row = _source("x1", content_source_type="x_account", external_id="persn_x", source_name="@persn")
    other = _source("s9", content_source_type="youtube_channel", external_id="UC_other", source_name="Unrelated")
    person = _personality(
        "p1",
        display_name="Persn",
        youtube_channel_ids=["UC_persn"],
        aliases=["persn_x"],
    )
    clusters = [_cluster("c1", cluster_slug="people")]
    # The cluster lists the personality AND (redundantly) the two raw rows + 1 other.
    members = [
        _pmember("c1", "p1", 0),
        _smember("c1", "yt1", 1),
        _smember("c1", "x1", 2),
        _smember("c1", "s9", 3),
    ]

    out = resolve_category_clusters("ai", clusters, members, [yt_row, x_row, other], [person])

    rendered = [(m.kind, m.followable_id) for m in out[0].members]
    # The bundled raw rows are gone; the card is present exactly once; unrelated stays.
    assert ("personality", "p1") in rendered
    assert rendered.count(("personality", "p1")) == 1
    assert ("source", "yt1") not in rendered
    assert ("source", "x1") not in rendered
    assert ("source", "s9") in rendered


def test_bundled_handles_suppressed_even_in_a_different_cluster() -> None:
    """A present personality suppresses its bundled source rows across EVERY cluster
    of the category, not just the cluster the personality sits in.

    WHY: the no-dup set is category-wide (Decision #7) — the raw row must not slip
    back in via a sibling cluster.
    """
    yt_row = _source("yt1", content_source_type="youtube_channel", external_id="UC_persn", source_name="Persn YT")
    person = _personality("p1", youtube_channel_ids=["UC_persn"])
    clusters = [
        _cluster("c1", cluster_slug="people", cluster_sort_order=0),
        _cluster("c2", cluster_slug="channels", cluster_sort_order=1),
    ]
    members = [_pmember("c1", "p1", 0), _smember("c2", "yt1", 0)]

    out = resolve_category_clusters("ai", clusters, members, [yt_row], [person])

    all_rendered = [(m.kind, m.followable_id) for c in out for m in c.members]
    assert ("source", "yt1") not in all_rendered
    # c2 is emptied by the suppression and dropped.
    assert [c.cluster_slug for c in out] == ["people"]


# ── (4) multi-category row ────────────────────────────────────────────────────
def test_source_in_two_categories_appears_under_each_call() -> None:
    """A source tagged to two categories is returned under each category call,
    deduped within a category.

    WHY: a followable can belong to clusters in different categories; the per-call
    resolver scopes dedup to ONE category, so it must still surface in both.
    """
    shared = _source("s1", source_name="Shared")
    clusters = [
        _cluster("c_ai", cluster_slug="ai-cluster", cluster_category="ai"),
        _cluster("c_tech", cluster_slug="tech-cluster", cluster_category="tech"),
    ]
    members = [_smember("c_ai", "s1", 0), _smember("c_tech", "s1", 0)]

    ai_out = resolve_category_clusters("ai", clusters, members, [shared], [])
    tech_out = resolve_category_clusters("tech", clusters, members, [shared], [])

    assert [m.followable_id for c in ai_out for m in c.members] == ["s1"]
    assert [m.followable_id for c in tech_out for m in c.members] == ["s1"]


# ── (5) member in two clusters of one category ────────────────────────────────
def test_followable_in_two_clusters_of_one_category_renders_once_first_wins() -> None:
    """A followable listed in two clusters of the SAME category renders once, in the
    FIRST cluster (by cluster order); the later cluster drops the duplicate.

    WHY: Decision #7 — each underlying followable appears once per category. The
    first-cluster-wins tiebreak keeps the result deterministic and never crashes.
    """
    shared = _source("s1", source_name="Shared")
    only_in_c2 = _source("s2", source_name="OnlyC2")
    clusters = [
        _cluster("c1", cluster_slug="first", cluster_sort_order=0),
        _cluster("c2", cluster_slug="second", cluster_sort_order=1),
    ]
    members = [
        _smember("c1", "s1", 0),
        _smember("c2", "s1", 0),  # duplicate — should be dropped from c2
        _smember("c2", "s2", 1),
    ]

    out = resolve_category_clusters("ai", clusters, members, [shared, only_in_c2], [])

    by_slug = {c.cluster_slug: [m.followable_id for m in c.members] for c in out}
    assert by_slug["first"] == ["s1"]  # first cluster wins the shared followable
    assert by_slug["second"] == ["s2"]  # c2 keeps only its unique member


def test_cluster_emptied_by_duplicate_dedup_is_dropped() -> None:
    """A cluster whose only member is a duplicate already rendered earlier is dropped.

    WHY: empty-after-dedup must apply AFTER first-cluster-wins, not before — a second
    cluster holding only the shared followable must not surface as an empty header.
    """
    shared = _source("s1")
    clusters = [
        _cluster("c1", cluster_slug="first", cluster_sort_order=0),
        _cluster("c2", cluster_slug="dup-only", cluster_sort_order=1),
    ]
    members = [_smember("c1", "s1", 0), _smember("c2", "s1", 0)]

    out = resolve_category_clusters("ai", clusters, members, [shared], [])

    assert [c.cluster_slug for c in out] == ["first"]
