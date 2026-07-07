"""Tests for the X theme production gather (slice #31).

Asserts the batch-side contract that makes theme reels REAL in production: the
followed-cluster join is per-user-honest (only X-cluster followers are eligible),
a cluster shared by many followers builds ONE reel story per theme (deterministic
id — shared cost), quiet/un-swept clusters fabricate nothing, and the R3 FK guard
drops unpersisted reel stories BEFORE assembly (a dangling id would fail a user's
entire batched daily_feeds insert). Mutation note: per-user leaks, duplicate
builds, or a surviving unpersisted candidate all fail below.
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest

from agents.pipeline.x_theme_ladder import XThemeAttribution, XThemeReelCandidate
from agents.pipeline.x_theme_production import (
    filter_placeable_theme_candidates,
    gather_x_theme_candidates,
)

_SWEEP_DATE = date(2026, 7, 7)


class _RoutedQuery:
    """Chainable query stub that applies eq/neq/in_ filters over seeded rows."""

    def __init__(self, table_name: str, store: dict[str, list[dict]]) -> None:
        self._table_name = table_name
        self._store = store
        self._eq: list[tuple[str, object]] = []
        self._neq: list[tuple[str, object]] = []
        self._in: list[tuple[str, set[str]]] = []

    def select(self, *_a, **_k) -> "_RoutedQuery":
        return self

    def eq(self, column: str, value: object) -> "_RoutedQuery":
        self._eq.append((column, value))
        return self

    def neq(self, column: str, value: object) -> "_RoutedQuery":
        self._neq.append((column, value))
        return self

    def in_(self, column: str, values: list) -> "_RoutedQuery":
        self._in.append((column, {str(v) for v in values}))
        return self

    def execute(self) -> SimpleNamespace:
        rows = self._store.get(self._table_name, [])
        for column, value in self._eq:
            rows = [r for r in rows if str(r.get(column)) == str(value)]
        for column, value in self._neq:
            rows = [r for r in rows if str(r.get(column)) != str(value)]
        for column, values in self._in:
            rows = [r for r in rows if str(r.get(column)) in values]
        return SimpleNamespace(data=list(rows))


class _RoutedClient:
    def __init__(self, store: dict[str, list[dict]]) -> None:
        self._store = store

    def table(self, name: str) -> _RoutedQuery:
        return _RoutedQuery(name, self._store)


def _theme_row(summary: str, handles: list[str]) -> dict:
    return {
        "theme_summary": summary,
        "supporting_handles": handles,
        "supporting_tweet_urls": [f"https://x.com/{h}/status/1" for h in handles],
    }


def _store_two_users_shared_cluster() -> dict[str, list[dict]]:
    """u1 + u2 both follow src-1 (x_account, member of clu-1); u2 also follows the
    quiet clu-2 via src-2; u3 follows only a YouTube channel (never eligible)."""
    return {
        "user_content_sources": [
            {"user_id": "u1", "source_id": "src-1", "source_priority": "normal"},
            {"user_id": "u2", "source_id": "src-1", "source_priority": "normal"},
            {"user_id": "u2", "source_id": "src-2", "source_priority": "normal"},
            {"user_id": "u3", "source_id": "src-yt", "source_priority": "normal"},
        ],
        "content_sources": [
            {"source_id": "src-1", "content_source_type": "x_account"},
            {"source_id": "src-2", "content_source_type": "x_account"},
            {"source_id": "src-yt", "content_source_type": "youtube_channel"},
        ],
        "source_cluster_members": [
            {"cluster_id": "clu-1", "source_id": "src-1"},
            {"cluster_id": "clu-2", "source_id": "src-2"},
        ],
        "source_clusters": [
            {"cluster_id": "clu-1", "cluster_label": "AI Insiders"},
            {"cluster_id": "clu-2", "cluster_label": "Markets Desk"},
        ],
        "x_cluster_sweeps": [
            {
                "cluster_id": "clu-1",
                "sweep_date": _SWEEP_DATE.isoformat(),
                "handle_count": 3,
                "raw_post_count": 12,
                "original_post_count": 9,
                "themes": [
                    _theme_row("AGI paper reactions", ["alice", "bob", "carol"]),
                    _theme_row("New chip export rules", ["alice", "dave"]),
                ],
            },
            # clu-2 was swept but found NO themes — an honest quiet day.
            {
                "cluster_id": "clu-2",
                "sweep_date": _SWEEP_DATE.isoformat(),
                "handle_count": 2,
                "raw_post_count": 1,
                "original_post_count": 1,
                "themes": [],
            },
        ],
    }


async def _null_renderer(_tweet_url: str) -> None:
    return None


@pytest.mark.asyncio
async def test_gather_builds_one_story_per_cluster_theme_shared_across_followers() -> (
    None
):
    """Happy path: shared cluster → ONE story per theme; both followers get candidates.

    WHY: cost stays flat with followers (spec §3 — the sweep AND the reel are shared);
    two users following clu-1 must reference the SAME deterministic story ids, and the
    rank must reflect how many handles converge (louder theme first in the ladder).
    """
    client = _RoutedClient(_store_two_users_shared_cluster())

    result = await gather_x_theme_candidates(
        client, ["u1", "u2", "u3"], _SWEEP_DATE, screenshot_renderer=_null_renderer
    )

    # ONE story per (cluster, theme) — 2 themes on clu-1, none on quiet clu-2.
    assert len(result.theme_stories) == 2
    story_ids = {s.canonical_story_id for s in result.theme_stories}
    assert all(
        sid.startswith(f"xtheme-clu-1-{_SWEEP_DATE.isoformat()}-") for sid in story_ids
    )
    # Both clu-1 followers carry the SAME reel story ids (shared, not per-user).
    u1_ids = [c.reel_story_id for c in result.candidates_by_user["u1"]]
    u2_ids = [c.reel_story_id for c in result.candidates_by_user["u2"]]
    assert set(u1_ids) == story_ids
    assert set(u2_ids) == story_ids
    # Rank = distinct backing handles (3-handle theme louder than the 2-handle one).
    ranks = {
        c.attribution.theme_summary: c.reel_rank
        for c in result.candidates_by_user["u1"]
    }
    assert ranks["AGI paper reactions"] == 3.0
    assert ranks["New chip export rules"] == 2.0
    # The YouTube-only follower is NOT eligible — absent, keeps the legacy x fill.
    assert "u3" not in result.candidates_by_user


@pytest.mark.asyncio
async def test_gather_quiet_only_cluster_user_stays_eligible_with_no_candidates() -> (
    None
):
    """Edge: a user whose ONLY cluster is quiet stays in the dict with an empty list.

    WHY: eligibility (follows an X cluster) and supply (themes today) are different
    facts — the eligible-but-quiet user must get the HONEST ladder (x slots to the
    news floor), never fall back to the legacy per-account x fill.
    """
    store = _store_two_users_shared_cluster()
    # u4 follows only the quiet clu-2.
    store["user_content_sources"].append(
        {"user_id": "u4", "source_id": "src-2", "source_priority": "normal"}
    )
    client = _RoutedClient(store)

    result = await gather_x_theme_candidates(
        client, ["u4"], _SWEEP_DATE, screenshot_renderer=_null_renderer
    )

    assert result.candidates_by_user == {"u4": []}
    assert result.theme_stories == []


@pytest.mark.asyncio
async def test_gather_no_x_follows_returns_empty_result() -> None:
    """Edge: no user follows any X source → empty gather (legacy fill everywhere)."""
    client = _RoutedClient({"user_content_sources": []})

    result = await gather_x_theme_candidates(
        client, ["u1"], _SWEEP_DATE, screenshot_renderer=_null_renderer
    )

    assert result.theme_stories == []
    assert result.candidates_by_user == {}


@pytest.mark.asyncio
async def test_gather_unswept_cluster_contributes_nothing() -> None:
    """Edge: a followed cluster with NO sweep row today fabricates no theme (PRD #30/#33)."""
    store = _store_two_users_shared_cluster()
    store["x_cluster_sweeps"] = []  # nothing swept today
    client = _RoutedClient(store)

    result = await gather_x_theme_candidates(
        client, ["u1", "u2"], _SWEEP_DATE, screenshot_renderer=_null_renderer
    )

    assert result.theme_stories == []
    # Both cluster followers remain eligible (honest ladder → news floor).
    assert result.candidates_by_user == {"u1": [], "u2": []}


def _candidate(story_id: str) -> XThemeReelCandidate:
    return XThemeReelCandidate(
        reel_story_id=story_id,
        cluster_id="clu-1",
        attribution=XThemeAttribution(
            theme_summary="theme", supporting_handles=["a", "b"]
        ),
        reel_rank=2.0,
    )


def test_filter_placeable_drops_unpersisted_and_keeps_eligible_users() -> None:
    """R3 FK guard: an unpersisted reel story never reaches assembly; users survive.

    WHY: daily_feeds.feed_story_id is NOT NULL REFERENCES stories — one dangling theme
    reel would fail the user's ENTIRE per-user batch insert. The user key must survive
    at zero candidates so the honest ladder (not the legacy fill) still owns their x slots.
    """
    candidates_by_user = {
        "u1": [_candidate("xtheme-ok"), _candidate("xtheme-halted")],
        "u2": [_candidate("xtheme-halted")],
    }

    filtered = filter_placeable_theme_candidates(candidates_by_user, {"xtheme-ok"})

    assert [c.reel_story_id for c in filtered["u1"]] == ["xtheme-ok"]
    assert filtered["u2"] == []  # user kept, candidate dropped


def test_filter_placeable_empty_input_is_noop() -> None:
    """Edge: empty candidates in → empty dict out (no phantom users)."""
    assert filter_placeable_theme_candidates({}, {"any"}) == {}
