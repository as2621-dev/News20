"""Offline tests for the per-archetype catalog seeder (Phase 5b SP3).

DoD (phase file SP3 / Rule 9 — tests encode WHY):
  - The seeder upserts ≥10 channels, ≥10 podcasts, ≥10 personalities tagged to
    archetypes. WHY: 5c's recommendation matcher reads a non-trivial curated
    catalog; a seeder that wrote <10 of a kind would leave an archetype with an
    empty grid.
  - Re-running is IDEMPOTENT — same unique keys, no duplicate rows. WHY: the
    seeder is a re-runnable curation tool (curators edit one archetype file and
    re-seed); a non-idempotent seeder would double-insert the whole catalog on
    every run and corrupt popularity ordering.
  - A channel handle resolves to a REAL external_id + thumbnail_url. WHY: the
    catalog row's identity is the resolved ``UC…`` channel id (the 0009 unique
    key) and the avatar is the resolved thumbnail — a seeder that stored the raw
    handle as external_id would never dedup against live-search rows in 5d.
  - personas use the 12 SP2 slugs; topic_tags use the 8 categories; the union of
    an overlapping source spans both archetype files. WHY: a mis-tagged source is
    invisible to (or mis-placed by) the persona-filtered browse in 5c.

All external services (YouTube / iTunes / Wikipedia HTTP, Supabase) are mocked at
the boundary (CLAUDE.md) — the suite runs fully offline with no keys.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from scripts.seed_catalog import seed_catalog
from scripts.seed_catalog.itunes_resolve import PodcastMeta
from scripts.seed_catalog.youtube_resolve import ChannelMeta

# ── Fakes (boundary mocks) ────────────────────────────────────────────────────


class FakeResponse:
    """A minimal stand-in for an ``httpx.Response`` carrying canned JSON."""

    def __init__(self, status_code: int, payload: Any) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class FakeHttpClient:
    """Routes ``.get`` to canned JSON by URL — covers YouTube, iTunes, Wikipedia.

    Each YouTube/iTunes call echoes back a deterministic resolved object derived
    from the request params, so a handle/term maps to a stable external_id and
    thumbnail (the DoD's "handle resolves to a real external_id + thumbnail").
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get(
        self, url: str, params: dict[str, Any] | None = None, timeout: float = 0.0
    ):
        self.calls.append(url)
        if "youtube" in url:
            return self._youtube(params or {})
        if "itunes" in url:
            return self._itunes(params or {})
        if "wikipedia" in url:
            return self._wikipedia(url)
        return FakeResponse(404, {})

    def _youtube(self, params: dict[str, Any]) -> FakeResponse:
        handle = params.get("forHandle") or params.get("id") or "unknown"
        return FakeResponse(
            200,
            {
                "items": [
                    {
                        "id": f"UC-{handle}",
                        "snippet": {
                            "title": f"{handle} (channel)",
                            "customUrl": f"@{handle}",
                            "description": "desc",
                            "thumbnails": {
                                "high": {"url": f"https://yt.test/{handle}.jpg"}
                            },
                        },
                        "statistics": {"subscriberCount": "123456"},
                    }
                ]
            },
        )

    def _itunes(self, params: dict[str, Any]) -> FakeResponse:
        term = params.get("term") or "unknown"
        return FakeResponse(
            200,
            {
                "results": [
                    {
                        "collectionId": 9000 + (abs(hash(term)) % 1000),
                        "collectionName": term,
                        "artistName": "Publisher",
                        "feedUrl": f"https://feeds.test/{abs(hash(term)) % 1000}.rss",
                        "artworkUrl600": f"https://itunes.test/{abs(hash(term)) % 1000}.jpg",
                        "trackCount": 42,
                    }
                ]
            },
        )

    def _wikipedia(self, url: str) -> FakeResponse:
        slug = url.rsplit("/", 1)[-1]
        return FakeResponse(
            200,
            {"originalimage": {"source": f"https://wiki.test/{slug}.jpg"}},
        )


class FakeUpsertQuery:
    """Captures an upsert payload + its ``on_conflict`` key, dedups on it."""

    def __init__(
        self, table: str, store: dict[str, dict[tuple[str, ...], dict[str, Any]]]
    ) -> None:
        self._table = table
        self._store = store
        self._rows: list[dict[str, Any]] = []
        self._conflict: tuple[str, ...] = ()

    def upsert(self, rows: list[dict[str, Any]], on_conflict: str) -> "FakeUpsertQuery":
        self._rows = rows
        self._conflict = tuple(part.strip() for part in on_conflict.split(","))
        return self

    def execute(self) -> Any:
        table_store = self._store.setdefault(self._table, {})
        for row in self._rows:
            # Reason: the unique key (the on_conflict columns) IS the row identity.
            # Re-upserting the same key overwrites in place — never appends — which
            # is exactly the idempotency the DoD asserts.
            key = tuple(str(row[col]) for col in self._conflict)
            table_store[key] = row
        from unittest.mock import MagicMock

        response = MagicMock()
        response.data = self._rows
        return response


class FakeSupabaseClient:
    """A mock Supabase client keying upserts on their ``on_conflict`` key.

    ``upserted_rows(table)`` returns the deduped rows so a test can assert the
    distinct row count is stable across re-runs (idempotency).
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[tuple[str, ...], dict[str, Any]]] = {}

    def table(self, table: str) -> FakeUpsertQuery:
        return FakeUpsertQuery(table, self._store)

    def upserted_rows(self, table: str) -> list[dict[str, Any]]:
        return list(self._store.get(table, {}).values())


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def supabase() -> FakeSupabaseClient:
    return FakeSupabaseClient()


@pytest.fixture
def http() -> FakeHttpClient:
    return FakeHttpClient()


def _run(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> seed_catalog.SeedSummary:
    """Run the seeder against the authored data dir with both clients mocked."""
    return asyncio.run(
        seed_catalog.run_seed(
            supabase_client=supabase,
            http_client=http,  # type: ignore[arg-type]
            youtube_api_key="TEST-KEY",
        )
    )


# ── DoD: ≥10 of each kind upserted, tagged to archetypes ──────────────────────


def test_seed_upserts_at_least_ten_of_each_kind(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: 5c reads a non-trivial curated catalog — <10 of a kind = empty grid."""
    summary = _run(supabase, http)

    content_sources = supabase.upserted_rows("content_sources")
    channels = [
        r for r in content_sources if r["content_source_type"] == "youtube_channel"
    ]
    podcasts = [r for r in content_sources if r["content_source_type"] == "podcast"]
    personalities = supabase.upserted_rows("personalities")

    assert len(channels) >= 10, f"expected >=10 distinct channels, got {len(channels)}"
    assert len(podcasts) >= 10, f"expected >=10 distinct podcasts, got {len(podcasts)}"
    assert len(personalities) >= 10, (
        f"expected >=10 distinct personalities, got {len(personalities)}"
    )
    assert summary.channels_upserted >= 10
    assert summary.podcasts_upserted >= 10
    assert summary.personalities_upserted >= 10


def test_every_row_is_tagged_with_valid_personas_and_topic_tags(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: a row tagged off the 12 SP2 slugs / 8 categories is invisible to 5c."""
    _run(supabase, http)
    rows = supabase.upserted_rows("content_sources") + supabase.upserted_rows(
        "personalities"
    )
    assert rows
    for row in rows:
        assert row["personas"], (
            f"row {row.get('external_id') or row.get('display_name')} has no personas"
        )
        for persona in row["personas"]:
            assert persona in seed_catalog.ALLOWED_ARCHETYPES, (
                f"bad persona {persona!r}"
            )
        for tag in row["topic_tags"]:
            assert tag in seed_catalog.ALLOWED_TOPIC_TAGS, f"bad topic_tag {tag!r}"


# ── DoD: a handle resolves to a real external_id + thumbnail_url ──────────────


def test_channel_handle_resolves_to_external_id_and_thumbnail(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: the catalog row's identity is the resolved UC… id, not the raw handle."""
    _run(supabase, http)
    channels = {
        r["external_id"]: r
        for r in supabase.upserted_rows("content_sources")
        if r["content_source_type"] == "youtube_channel"
    }
    # lexfridman is authored in two archetype files — it must resolve once.
    row = channels["UC-lexfridman"]
    assert row["external_id"] == "UC-lexfridman"
    assert row["thumbnail_url"] == "https://yt.test/lexfridman.jpg"
    assert row["subscriber_count"] == 123456
    assert not row["external_id"].startswith("@"), (
        "external_id must be the channel id, not the handle"
    )


def test_podcast_feed_url_captured_into_platform_metadata(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: 5d ingestion reads platform_metadata.feed_url to pull RSS episodes."""
    _run(supabase, http)
    podcasts = [
        r
        for r in supabase.upserted_rows("content_sources")
        if r["content_source_type"] == "podcast"
    ]
    assert podcasts
    for row in podcasts:
        assert row["external_id"].startswith("itunes-"), (
            "podcast external_id must be itunes-prefixed"
        )
        assert row["platform_metadata"]["feed_url"].startswith("https://feeds.test/")


# ── DoD: persona-union across archetype files ─────────────────────────────────


def test_overlapping_source_unions_personas_across_archetype_files(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: a source curated under two archetypes must surface in BOTH 5c grids."""
    _run(supabase, http)

    channels = {
        r["external_id"]: r
        for r in supabase.upserted_rows("content_sources")
        if r["content_source_type"] == "youtube_channel"
    }
    # lexfridman appears in channels.ai-frontier-tech AND channels.tech-generalist.
    lex = channels["UC-lexfridman"]
    assert set(lex["personas"]) >= {"ai-frontier-tech", "tech-generalist"}

    personalities = {
        r["display_name"]: r for r in supabase.upserted_rows("personalities")
    }
    # Sam Altman appears in personalities.ai-frontier-tech AND .startup-operator.
    altman = personalities["Sam Altman"]
    assert set(altman["personas"]) >= {"ai-frontier-tech", "startup-operator"}


# ── DoD: personalities ALSO land in content_sources (the People swipe deck) ───


def test_personalities_also_seeded_as_content_sources_for_people_deck(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: the 5c People swipe deck reads ``content_sources`` rows of
    ``content_source_type='personality'`` (uniform with the other 3 axes), NOT the
    donor ``personalities`` table. Without these rows ``listSourcesByArchetype``
    returns nothing and the People grid renders empty — the exact bug this seeds
    away. The persona tags must be present (the deck filters on persona overlap)
    and the Wikipedia photo must carry over as the card avatar."""
    summary = _run(supabase, http)

    people = [
        r
        for r in supabase.upserted_rows("content_sources")
        if r["content_source_type"] == "personality"
    ]
    assert people, (
        "no content_sources personality rows — the People swipe deck reads these; "
        "seeding only the personalities table leaves the grid empty"
    )
    assert summary.personality_content_sources_upserted == len(people)
    # One content_sources row per DISTINCT person — the same count as the donor
    # personalities table (Sam Altman is in two files → unioned to a single row).
    assert len(people) == len(supabase.upserted_rows("personalities"))
    for row in people:
        # listSourcesByArchetype filters `personas && [archetype]` — an empty
        # personas array can never overlap, so the card would be invisible.
        assert row["personas"], f"{row['source_name']} has no personas → invisible"
        for persona in row["personas"]:
            assert persona in seed_catalog.ALLOWED_ARCHETYPES
        # the resolved Wikipedia photo is the People card's avatar
        assert row["thumbnail_url"] and row["thumbnail_url"].startswith(
            "https://wiki.test/"
        )
        # people have no follower count → the deck renders no count label
        assert row["subscriber_count"] is None


# ── DoD: idempotency — re-run yields the same distinct row set ────────────────


def test_re_running_is_idempotent_no_duplicate_rows(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: the seeder is a re-runnable curation tool — a 2nd run must not double rows."""
    first = _run(supabase, http)
    channels_after_first = len(supabase.upserted_rows("content_sources"))
    personalities_after_first = len(supabase.upserted_rows("personalities"))

    second = _run(supabase, FakeHttpClient())

    assert len(supabase.upserted_rows("content_sources")) == channels_after_first
    assert len(supabase.upserted_rows("personalities")) == personalities_after_first
    assert second.channels_upserted == first.channels_upserted
    assert second.podcasts_upserted == first.podcasts_upserted
    assert second.personalities_upserted == first.personalities_upserted


# ── X accounts stored WITHOUT resolution ──────────────────────────────────────


def test_x_accounts_stored_without_live_resolution(
    supabase: FakeSupabaseClient, http: FakeHttpClient
) -> None:
    """WHY: no X resolver exists until 5c/5d — handle is the external_id, no thumbnail."""
    _run(supabase, http)
    x_rows = [
        r
        for r in supabase.upserted_rows("content_sources")
        if r["content_source_type"] == "x_account"
    ]
    assert x_rows, "expected x_account rows from x.*.json"
    for row in x_rows:
        assert not row["external_id"].startswith("@"), (
            "x external_id is the bare handle"
        )
        assert row["thumbnail_url"] is None, (
            "no thumbnail fetch for x_account (resolver is 5c/5d)"
        )


# ── Failure case: a 404 channel is skipped, not fatal ─────────────────────────


def test_unresolved_channel_is_skipped_not_fatal(supabase: FakeSupabaseClient) -> None:
    """WHY: one dead handle must not abort the whole batch (Rule 12 — fail per row)."""

    class MissHttpClient(FakeHttpClient):
        def _youtube(self, params: dict[str, Any]) -> FakeResponse:
            return FakeResponse(200, {"items": []})  # every channel misses

    miss = MissHttpClient()
    summary = asyncio.run(
        seed_catalog.run_seed(
            supabase_client=supabase,
            http_client=miss,  # type: ignore[arg-type]
            youtube_api_key="TEST-KEY",
            type_filter="channels",
        )
    )
    assert summary.channels_upserted == 0
    assert summary.channels_unresolved >= 10
    # The run completed (no exception) despite every channel missing.


# ── Popularity score derives from file rank ───────────────────────────────────


def test_popularity_score_decreases_with_file_rank() -> None:
    """WHY: file position IS the popularity rank — 5c orders the grid by it."""
    top = seed_catalog.CatalogEntry(dedup_key="a", entry_type="channels", rank=0)
    mid = seed_catalog.CatalogEntry(dedup_key="b", entry_type="channels", rank=5)
    deep = seed_catalog.CatalogEntry(dedup_key="c", entry_type="channels", rank=1000)
    assert top.popularity_score > mid.popularity_score
    assert deep.popularity_score == seed_catalog.POPULARITY_FLOOR
    assert top.popularity_score == seed_catalog.POPULARITY_TOP


# ── Dry-run writes nothing ────────────────────────────────────────────────────


def test_dry_run_resolves_but_writes_nothing(http: FakeHttpClient) -> None:
    """WHY: --dry-run lets a curator preview resolution without touching the DB."""
    summary = asyncio.run(
        seed_catalog.run_seed(
            supabase_client=None,
            http_client=http,  # type: ignore[arg-type]
            youtube_api_key="TEST-KEY",
            dry_run=True,
        )
    )
    # Counts still report what WOULD be written, but no client was touched.
    assert summary.channels_upserted >= 10


# ── Resolver unit sanity (Pydantic mapping) ───────────────────────────────────


def test_channel_meta_maps_api_item() -> None:
    """WHY: a field-mapping regression silently corrupts every channel row."""
    meta = ChannelMeta(
        channel_id="UC123",
        title="T",
        thumbnail_url="https://x/y.jpg",
        subscriber_count=10,
    )
    assert meta.channel_id == "UC123"


def test_podcast_meta_external_id_is_itunes_prefixed() -> None:
    """WHY: the itunes- prefix is the cross-entry-point dedup key (5c/5d)."""
    meta = PodcastMeta(collection_id=555, name="P")
    assert meta.external_id == "itunes-555"
