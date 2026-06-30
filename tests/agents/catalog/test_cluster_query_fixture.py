"""Milestone-level integration test for Phase FSR-M1 SP4 (the OFFLINE de-risking check).

This is M1's riskiest-assumption gate: it proves the no-dup trust contract (PRD
Decision #7 — a person is shown once, never as both a card and their raw handle rows)
holds through the FULL ``clusters_for_category`` path over a COMMITTED catalog
fixture, not merely in the SP2 resolver unit. If this regresses, onboarding would
show duplicates/randoms — the exact failure M1 exists to de-risk.

Pure: loads ``fixtures/catalog_fixture.json`` into an in-memory repo, NO DB / network
/ clock. Each test's docstring encodes WHY the rule matters (Rule 9).
"""

from __future__ import annotations

import json
from pathlib import Path

from agents.catalog.cluster_query import InMemoryCatalogRepo, clusters_for_category
from agents.catalog.models import (
    CatalogSourceRow,
    ClusterMemberRef,
    ClusterRow,
    PersonalityRow,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "catalog_fixture.json"


def _load_repo() -> InMemoryCatalogRepo:
    """Parse the committed fixture into the SP2 models + the in-memory repo.

    Extra documentation keys in the JSON (``_note``/``_doc``) are ignored by the
    Pydantic models, so the fixture can stay self-describing without polluting rows.
    """
    data = json.loads(_FIXTURE.read_text())
    return InMemoryCatalogRepo(
        clusters=[ClusterRow(**d) for d in data["clusters"]],
        members=[ClusterMemberRef(**d) for d in data["members"]],
        sources=[CatalogSourceRow(**d) for d in data["sources"]],
        personalities=[PersonalityRow(**d) for d in data["personalities"]],
    )


# ── the load-bearing no-dup contract, end-to-end ──────────────────────────────
def test_ai_no_dup_rule_holds_through_full_path() -> None:
    """clusters_for_category('ai') excludes the personality's bundled source rows and
    shows the personality card exactly once.

    WHY: this is the M1 de-risking check. The bundled YouTube channel (s-demis-yt) and
    X account (s-demis-x) exist as content_sources rows AND are listed as cluster
    members, yet because the personality (p-demis) bundles them, they must NOT render
    anywhere in the AI result — only the personality card does. A leak here is the
    "person shown twice" trust-contract break the whole milestone guards against. This
    asserts the rule holds through the repo→resolver path, not just the SP2 unit.
    """
    out = clusters_for_category("ai", repo=_load_repo())

    members = [(m.kind, m.followable_id) for c in out for m in c.members]

    # The two bundled raw source rows are absent everywhere in the AI result.
    assert ("source", "s-demis-yt") not in members
    assert ("source", "s-demis-x") not in members
    # The personality appears exactly once, as a personality card.
    assert members.count(("personality", "p-demis")) == 1


def test_ai_members_in_member_sort_order() -> None:
    """Members within each AI cluster render in member_sort_order.

    WHY: the onboarding grid is an ORDERED bulk-select; the editor's sequence is the
    product signal. The first AI cluster lists (after suppressing the two bundled rows)
    the personality, then s-shared, then s-multi — in that authored order.
    """
    out = clusters_for_category("ai", repo=_load_repo())

    first = next(c for c in out if c.cluster_slug == "ai-lab-leaders")
    # member_sort_order: p-demis(0), s-demis-yt(1, suppressed), s-demis-x(2, suppressed),
    # s-shared(3), s-multi(4) → rendered order is p-demis, s-shared, s-multi.
    assert [m.followable_id for m in first.members] == ["p-demis", "s-shared", "s-multi"]


def test_category_with_no_clusters_returns_empty_list() -> None:
    """A category with no clusters returns [] — not a crash.

    WHY: the fixture has zero 'sport' clusters; onboarding must render an empty section
    gracefully, never raise. Guards the empty-path the live repo will hit for thin
    categories.
    """
    assert clusters_for_category("sport", repo=_load_repo()) == []


def test_empty_cluster_absent_from_ai_result() -> None:
    """The AI cluster whose only member is un-curated is NOT in the result.

    WHY: an un-curated member renders nothing, emptying its cluster; a 0-member cluster
    must not surface as a broken header with no rows.
    """
    out = clusters_for_category("ai", repo=_load_repo())

    assert "ai-empty" not in {c.cluster_slug for c in out}


def test_shared_followable_renders_once_first_cluster_wins() -> None:
    """A source listed in two AI clusters renders once, in the first cluster (by order).

    WHY: Decision #7 — each underlying followable appears once per category. s-shared is
    a member of both ai-lab-leaders (sort 0) and ai-founders (sort 1); it must render
    only in ai-lab-leaders, and ai-founders keeps only its unique member (s-founder).
    """
    out = clusters_for_category("ai", repo=_load_repo())

    by_slug = {c.cluster_slug: [m.followable_id for m in c.members] for c in out}
    all_rendered = [fid for ids in by_slug.values() for fid in ids]

    assert all_rendered.count("s-shared") == 1
    assert "s-shared" in by_slug["ai-lab-leaders"]
    assert "s-shared" not in by_slug["ai-founders"]
    assert by_slug["ai-founders"] == ["s-founder"]
