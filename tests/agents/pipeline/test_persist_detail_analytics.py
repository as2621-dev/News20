"""Unit tests for the Phase 2c SP4 persist + segment-resolution wiring.

WHY these tests (Rule 9 — they encode intent, not just behaviour):
  - ``persist_digest`` must write each NEW Phase 2c table (``story_timeline``,
    ``story_analytics``, ``detail_key_points``, ``stories.story_key_figure_*``, the
    ``story_trust`` reach columns) with a CORRECTLY-SHAPED, ORDERED payload. A
    swapped column, a dropped row, an out-of-order index, or a raw-dict
    ``analytic_rows`` element would fail here.
  - ``_resolve_segment_slug`` must resolve a geopolitics-matched story to a
    NON-``wildcard`` segment (the SP3 stub always returned ``wildcard``, which made
    the segment-skinned tab a no-op). The test fails if the stub is restored.

The supabase client is mocked at the boundary (CLAUDE.md mandate) — no network.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from agents.ingestion.models import CanonicalStory, StoryInterestTag
from agents.pipeline.models import (
    AnalyticRow,
    DetailKeyPoint,
    DetailTimelineEvent,
    DialogueTurn,
    DigestScript,
    KeyFigure,
    SecondAnalytic,
)
from agents.pipeline.persist import _resolve_segment_slug, persist_digest
from agents.pipeline.persist_helpers import resolve_segment_from_tags
from agents.pipeline.stages.detail_enrichment import DetailEnrichment
from agents.pipeline.stages.forced_alignment import align_transcript_to_audio

_NOW = datetime(2026, 5, 31, 12, 0, 0, tzinfo=timezone.utc)


class FakeStorageBucket:
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def upload(self, path: str, file: bytes, file_options: dict) -> None:  # noqa: ARG002
        return None

    def get_public_url(self, path: str) -> str:
        return f"https://storage.test/{self.bucket}/{path}"


class FakeTableQuery:
    """Captures insert payloads + echoes back synthetic uuid PKs."""

    _PK_BY_TABLE = {
        "digests": "digest_id",
        "caption_sentences": "caption_sentence_id",
        "detail_chunks": "detail_chunk_id",
        "story_trust": "story_trust_id",
        "story_sources": "story_source_id",
        "story_interests": "story_interest_id",
        "suggested_questions": "suggested_question_id",
        "story_timeline": "story_timeline_id",
        "story_analytics": "story_analytic_id",
        "detail_key_points": "detail_key_point_id",
    }

    def __init__(self, table: str, captured: dict) -> None:
        self.table = table
        self.captured = captured
        self._rows: list[dict] = []

    def insert(self, rows: list[dict]) -> "FakeTableQuery":
        self.captured.setdefault(self.table, []).extend(rows)
        pk = self._PK_BY_TABLE.get(self.table)
        self._rows = [
            {**row, **({pk: f"{self.table}-uuid-{i}"} if pk else {})}
            for i, row in enumerate(rows)
        ]
        return self

    def execute(self) -> MagicMock:
        response = MagicMock()
        response.data = self._rows
        return response


class FakeSupabaseClient:
    def __init__(self) -> None:
        self.captured_inserts: dict[str, list[dict]] = {}
        self.storage = MagicMock()
        self.storage.from_ = lambda bucket: FakeStorageBucket(bucket)

    def table(self, table: str) -> FakeTableQuery:
        return FakeTableQuery(table, self.captured_inserts)


@pytest.fixture
def canonical_story() -> CanonicalStory:
    return CanonicalStory(
        canonical_story_id="cand-iran-001",
        canonical_title="U.S. strikes Iran again as a deal looks close",
        canonical_url="https://cnn.com/iran",
        canonical_normalized_url="https://cnn.com/iran",
        canonical_published_utc=_NOW,
        canonical_primary_outlet_domain="cnn.com",
        canonical_primary_outlet_name="CNN",
        canonical_body_text="The U.S. struck a second site inside Iran overnight.",
        covering_outlets=["cnn.com", "reuters.com", "foxnews.com", "bbc.com"],
        story_outlet_count=4,
    )


@pytest.fixture
def digest_script() -> DigestScript:
    return DigestScript(
        digest_story_id="cand-iran-001",
        turns=[
            DialogueTurn(speaker="ALEX", text="What happened with Iran?"),
            DialogueTurn(speaker="JORDAN", text="The U.S. struck a second site."),
        ],
        word_count=10,
        estimated_duration_seconds=4,
    )


@pytest.fixture
def story_interest_tags() -> list[StoryInterestTag]:
    # Leaf (depth 0) world story → maps to geopolitics in the lookup.
    return [
        StoryInterestTag(
            story_interest_story_id="cand-iran-001",
            story_interest_interest_id="int-world",
            story_interest_match_depth=0,
        ),
    ]


@pytest.fixture
def enrichment() -> DetailEnrichment:
    """A grounded geopolitics enrichment (market_impact analytic, 5 key points)."""
    return DetailEnrichment(
        enrichment_story_id="cand-iran-001",
        key_figure=KeyFigure(
            key_figure_value="~20%", key_figure_label="of global oil transits Hormuz"
        ),
        timeline=[
            DetailTimelineEvent(
                timeline_event_index=0,
                timeline_when_label="08:10",
                timeline_what_text="U.S. confirms an overnight strike inside Iran.",
            ),
            DetailTimelineEvent(
                timeline_event_index=1,
                timeline_when_label="13:40",
                timeline_what_text="Iran issues new transit rules for Hormuz.",
            ),
        ],
        second_analytic=SecondAnalytic(
            analytic_story_id="cand-iran-001",
            analytic_kind="market_impact",
            analytic_tab_label="MARKET IMPACT",
            analytic_headline="Oil markets brace on Hormuz tension",
            analytic_summary_text="A closure would choke ~20% of seaborne crude.",
            analytic_rows=[
                AnalyticRow(
                    analytic_row_label="Hormuz oil share",
                    analytic_row_value="~20%",
                    analytic_row_note="of global transit",
                ),
                AnalyticRow(
                    analytic_row_label="Brent crude",
                    analytic_row_value=None,
                    analytic_row_direction="up",
                ),
            ],
            analytic_is_grounded=True,
        ),
        key_points=[
            DetailKeyPoint(key_point_index=i, key_point_text=f"Key point {i}.")
            for i in range(5)
        ],
    )


def _make_track(story: CanonicalStory):
    return align_transcript_to_audio(
        digest_id=story.canonical_story_id,
        sentences=["The U.S. struck a second site inside Iran."],
        audio_duration_s=5.0,
    )


class TestResolveSegmentSlug:
    """The SP3 stub always returned wildcard; SP4 must resolve the real segment."""

    def test_geopolitics_story_resolves_non_wildcard(self, story_interest_tags) -> None:
        """A world-matched story resolves to geopolitics (NOT wildcard) — Rule 9."""
        lookup = {"int-world": "geopolitics"}
        assert _resolve_segment_slug(story_interest_tags, lookup) == "geopolitics"
        assert _resolve_segment_slug(story_interest_tags, lookup) != "wildcard"

    def test_no_lookup_falls_back_to_wildcard(self, story_interest_tags) -> None:
        """No injected lookup → wildcard (the safe catch-all)."""
        assert _resolve_segment_slug(story_interest_tags, None) == "wildcard"
        assert _resolve_segment_slug(story_interest_tags, {}) == "wildcard"

    def test_closest_match_depth_wins(self) -> None:
        """The lowest-match-depth (leaf) tag characterizes the segment."""
        tags = [
            StoryInterestTag(
                story_interest_story_id="s",
                story_interest_interest_id="int-sport",
                story_interest_match_depth=2,
            ),
            StoryInterestTag(
                story_interest_story_id="s",
                story_interest_interest_id="int-markets",
                story_interest_match_depth=0,
            ),
        ]
        lookup = {"int-sport": "sport", "int-markets": "markets"}
        # The depth-0 markets tag is closest → markets, not sport.
        assert resolve_segment_from_tags(tags, lookup) == "markets"


class TestPersistDetailAnalytics:
    """persist_digest writes every Phase 2c table correctly + ordered (DoD)."""

    def test_persist_writes_all_new_tables_ordered(
        self, canonical_story, digest_script, story_interest_tags, enrichment
    ) -> None:
        client = FakeSupabaseClient()
        result = persist_digest(
            supabase_client=client,
            story=canonical_story,
            script=digest_script,
            caption_track=_make_track(canonical_story),
            audio_bytes=b"FAKE-MP3",
            audio_duration_ms=55000,
            story_interest_tags=story_interest_tags,
            story_id="FIXTURE-SP4-iran",
            enrichment=enrichment,
            interest_segment_lookup={"int-world": "geopolitics"},
        )
        inserts = client.captured_inserts

        # stories: the resolved segment is geopolitics (NOT wildcard) + key figure.
        story_row = inserts["stories"][0]
        assert story_row["story_segment_slug"] == "geopolitics"
        assert story_row["story_key_figure_value"] == "~20%"
        assert story_row["story_key_figure_label"] == "of global oil transits Hormuz"

        # story_timeline: contiguous index order, right columns.
        timeline_rows = inserts["story_timeline"]
        assert [r["timeline_event_index"] for r in timeline_rows] == [0, 1]
        assert timeline_rows[0]["timeline_story_id"] == "FIXTURE-SP4-iran"
        assert timeline_rows[0]["timeline_when_label"] == "08:10"
        assert timeline_rows[1]["timeline_what_text"].startswith("Iran issues")

        # story_analytics (1:1): segment-correct kind + JSONB rows are plain dicts
        # (validated through AnalyticRow.model_dump — never raw dicts at the boundary).
        analytics_row = inserts["story_analytics"][0]
        assert analytics_row["analytic_story_id"] == "FIXTURE-SP4-iran"
        assert analytics_row["analytic_kind"] == "market_impact"
        assert analytics_row["analytic_tab_label"] == "MARKET IMPACT"
        assert analytics_row["analytic_is_grounded"] is True
        rows = analytics_row["analytic_rows"]
        assert isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
        assert rows[0]["analytic_row_label"] == "Hormuz oil share"
        assert rows[0]["analytic_row_value"] == "~20%"
        # Direction-only row (ungrounded number dropped upstream) survives as None.
        assert rows[1]["analytic_row_value"] is None
        assert rows[1]["analytic_row_direction"] == "up"

        # detail_key_points: exactly 5, 0-based contiguous order.
        kp_rows = inserts["detail_key_points"]
        assert len(kp_rows) == 5
        assert [r["key_point_index"] for r in kp_rows] == [0, 1, 2, 3, 4]
        assert all(r["key_point_story_id"] == "FIXTURE-SP4-iran" for r in kp_rows)

        # audit counts.
        assert result.timeline_event_count == 2
        assert result.detail_key_point_count == 5
        assert result.story_analytics_written is True

    def test_persist_without_enrichment_skips_new_tables(
        self, canonical_story, digest_script, story_interest_tags
    ) -> None:
        """No enrichment → the new tables are not written; key figure stays null."""
        client = FakeSupabaseClient()
        persist_digest(
            supabase_client=client,
            story=canonical_story,
            script=digest_script,
            caption_track=_make_track(canonical_story),
            audio_bytes=b"FAKE",
            audio_duration_ms=46000,
            story_interest_tags=story_interest_tags,
            story_id="FIXTURE-SP4-noenrich",
        )
        inserts = client.captured_inserts
        assert "story_analytics" not in inserts
        assert "story_timeline" not in inserts
        assert "detail_key_points" not in inserts
        assert inserts["stories"][0]["story_key_figure_value"] is None
        # No coverage_report → coverage_mode column omitted (DB default partisan).
        assert "coverage_mode" not in inserts["story_trust"][0]

    def test_persist_writes_reach_coverage_columns_from_report(
        self, canonical_story, digest_script, story_interest_tags, enrichment
    ) -> None:
        """A reach CoverageReport populates the story_trust reach columns (DoD)."""
        from agents.pipeline.models import CoverageReport

        report = CoverageReport(
            coverage_mode="reach",
            coverage_outlet_count=23,
            coverage_momentum="developing",
            coverage_originating_outlet_name="Reuters",
            coverage_notable_outlet_names=["Reuters", "BBC News", "AP"],
        )
        client = FakeSupabaseClient()
        persist_digest(
            supabase_client=client,
            story=canonical_story,
            script=digest_script,
            caption_track=_make_track(canonical_story),
            audio_bytes=b"FAKE",
            audio_duration_ms=55000,
            story_interest_tags=story_interest_tags,
            story_id="FIXTURE-SP4-reach",
            enrichment=enrichment,
            coverage_report=report,
        )
        trust_row = client.captured_inserts["story_trust"][0]
        assert trust_row["coverage_mode"] == "reach"
        assert trust_row["coverage_outlet_count"] == 23
        assert trust_row["coverage_momentum"] == "developing"
        assert trust_row["coverage_originating_outlet_name"] == "Reuters"
        assert trust_row["coverage_notable_outlet_names"] == [
            "Reuters",
            "BBC News",
            "AP",
        ]


class TestLoadOutletsLookup:
    """The outlets_lookup loader maps domain→lean from a mocked outlets read."""

    def test_loads_only_rated_domain_rows(self) -> None:
        """Rows without a domain OR a lean are skipped; domains are lowercased."""
        from agents.pipeline.persist_helpers import load_outlets_lookup

        response = MagicMock()
        response.data = [
            {"outlet_domain": "CNN.com", "outlet_bias_lean": "left"},
            {"outlet_domain": "foxnews.com", "outlet_bias_lean": "right"},
            {"outlet_domain": None, "outlet_bias_lean": "center"},  # no domain → skip
            {"outlet_domain": "noname.com", "outlet_bias_lean": None},  # no lean → skip
        ]
        table_query = MagicMock()
        table_query.select.return_value.execute.return_value = response
        client = MagicMock()
        client.table.return_value = table_query

        lookup = load_outlets_lookup(client)
        assert lookup == {"cnn.com": "left", "foxnews.com": "right"}
