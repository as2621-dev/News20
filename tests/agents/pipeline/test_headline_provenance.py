"""Headline provenance: every published reel carries an informative sentence.

WHY these tests exist (Rule 9): the prod 07-07 batch shipped a reel headlined
literally **"Language Magazine"** — a source masthead, not a headline (PRD RC4).
Three fail-open seams let it through: GDELT's ``<PAGE_TITLE>`` can be a masthead,
the cluster representative was the *earliest-published* member (so the worst title
won), and the editorial rewrite fell back to the original title on any failure.

A test that passes while a masthead can reach ``stories.story_headline`` is wrong:
these assert the bad title is **rejected or replaced**, never published.

Pure functions + mocked LLM/Supabase — no network, no clock.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from agents.ingestion.dedup import StoryClusterer
from agents.ingestion.models import CandidateStory, CanonicalStory, StoryInterestTag
from agents.pipeline.llm_clients import LLMClient
from agents.pipeline.models import DialogueTurn, DigestScript
from agents.pipeline.orchestrator import write_phase
from agents.pipeline.persist import persist_digest
from agents.pipeline.persist_helpers import reject_unpublishable_headline
from agents.pipeline.stages.forced_alignment import align_transcript_to_audio
from agents.pipeline.stages.editorial import run_editorial_rewrite
from agents.shared.exceptions import HeadlineQualityError
from agents.shared.headline_quality import (
    MIN_HEADLINE_WORD_COUNT,
    headline_rejection_reason,
    is_publishable_headline,
)

_EARLIER = datetime(2026, 7, 7, 9, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)

_MASTHEAD = "Language Magazine"
_REAL_HEADLINE = "Bilingual schools report a surge in enrollment"


def _candidate(
    external_id: str,
    title: str,
    url: str,
    *,
    published_utc: datetime,
    outlet_domain: str = "languagemagazine.com",
    outlet_name: str = "Language Magazine",
) -> CandidateStory:
    """One GDELT candidate row (only the fields clustering + headlines read)."""
    return CandidateStory(
        candidate_external_id=external_id,
        candidate_title=title,
        candidate_url=url,
        candidate_outlet_domain=outlet_domain,
        candidate_outlet_name=outlet_name,
        candidate_published_utc=published_utc,
    )


class TestHeadlineSanityGate:
    """The persist-time sanity gate: masthead / fragment titles are not headlines."""

    def test_informative_sentence_is_publishable(self) -> None:
        """The whole point of the gate is that real headlines sail through it."""
        assert is_publishable_headline(
            "Iran halts uranium enrichment after the Vienna talks collapse",
            outlet_name="Reuters",
            outlet_domain="reuters.com",
        )

    def test_masthead_matching_a_subdomained_host_is_rejected(self) -> None:
        """GDELT hands us hosts with subdomains, and on that path the outlet NAME is
        just the domain echoed back — so the domain is what a masthead has to be
        matched against. "Times of India" clears the word floor; only the domain
        match catches it, and only if subdomain labels are considered.
        """
        assert (
            headline_rejection_reason(
                "Times of India", outlet_domain="timesofindia.indiatimes.com"
            )
            == "title_equals_outlet"
        )

    def test_title_equal_to_outlet_name_is_rejected(self) -> None:
        """The exact "Language Magazine" regression — the title IS the masthead."""
        reason = headline_rejection_reason(
            "Language Magazine",
            outlet_name="Language Magazine",
            outlet_domain="languagemagazine.com",
        )
        assert reason == "title_equals_outlet"

    def test_sub_floor_word_count_is_rejected(self) -> None:
        """A fragment carries no news — "Breaking News" is a label, not a headline."""
        assert (
            headline_rejection_reason(
                "Breaking News", outlet_name="CNN", outlet_domain="cnn.com"
            )
            == "too_few_words"
        )

    def test_short_but_informative_headline_at_the_floor_passes(self) -> None:
        """The floor must not be so blunt it kills good short headlines.

        Exactly ``MIN_HEADLINE_WORD_COUNT`` words, subject + verb + object — this is
        a real headline and must publish. Raising the floor breaks this test on
        purpose: the constant is a contract, not a knob to tighten silently.
        """
        headline = "Musk buys Twitter"
        assert len(headline.split()) == MIN_HEADLINE_WORD_COUNT
        assert is_publishable_headline(
            headline, outlet_name="Reuters", outlet_domain="reuters.com"
        )


class TestClusterRepresentativeByTitleQuality:
    """The cluster's title comes from its best-titled member, not its earliest one."""

    def test_masthead_member_loses_to_the_informative_member(self) -> None:
        """Mixed-quality cluster → the real headline wins.

        The masthead row is the EARLIER one, so this fails the moment representative
        selection reverts to earliest-published (RC4's "worst title wins").
        """
        candidates = [
            _candidate(
                "gdelt-masthead",
                _MASTHEAD,
                "https://languagemagazine.com/article?utm_source=x",
                published_utc=_EARLIER,
            ),
            _candidate(
                "gdelt-real",
                _REAL_HEADLINE,
                "https://languagemagazine.com/article",
                published_utc=_LATER,
            ),
        ]

        stories = StoryClusterer().cluster_candidates(candidates)

        assert len(stories) == 1
        assert stories[0].canonical_title == _REAL_HEADLINE
        assert stories[0].canonical_representative_external_id == "gdelt-real"

    def test_all_members_publishable_keeps_the_earliest(self) -> None:
        """Quality is the tie-break's PREFIX, not a replacement for it.

        With nothing to separate the titles on quality, the long-standing
        earliest-published rule still decides — so freshness/first-reported
        semantics elsewhere are untouched for the ordinary cluster.
        """
        candidates = [
            _candidate(
                "gdelt-late",
                "Bilingual schools expand their dual-language programs",
                "https://languagemagazine.com/b?ref=home",
                published_utc=_LATER,
            ),
            _candidate(
                "gdelt-early",
                _REAL_HEADLINE,
                "https://languagemagazine.com/b",
                published_utc=_EARLIER,
            ),
        ]

        stories = StoryClusterer().cluster_candidates(candidates)

        assert len(stories) == 1
        assert stories[0].canonical_representative_external_id == "gdelt-early"

    def test_cluster_published_time_stays_the_earliest_member(self) -> None:
        """First-reported time is the cluster's, not the title-winner's.

        ``canonical_published_utc`` feeds the freshness gate, the ranking recency
        component, and the persisted ``story_first_reported_utc`` — a better title
        arriving three hours later must not make the event look newer.
        """
        candidates = [
            _candidate(
                "gdelt-masthead",
                _MASTHEAD,
                "https://languagemagazine.com/c?utm_medium=rss",
                published_utc=_EARLIER,
            ),
            _candidate(
                "gdelt-real",
                _REAL_HEADLINE,
                "https://languagemagazine.com/c",
                published_utc=_LATER,
            ),
        ]

        stories = StoryClusterer().cluster_candidates(candidates)

        assert stories[0].canonical_published_utc == _EARLIER


def _canonical_story(title: str = _REAL_HEADLINE) -> CanonicalStory:
    """One post-verification canonical story with a body (editorial's precondition)."""
    return CanonicalStory(
        canonical_story_id="cand-lang-001",
        canonical_title=title,
        canonical_url="https://languagemagazine.com/article",
        canonical_normalized_url="https://languagemagazine.com/article",
        canonical_published_utc=_EARLIER,
        canonical_primary_outlet_domain="languagemagazine.com",
        canonical_primary_outlet_name="Language Magazine",
        canonical_body_text="Enrollment in dual-language programs rose 18% this year.",
        canonical_representative_external_id="gdelt-real",
        covering_outlets=["languagemagazine.com"],
        story_outlet_count=1,
    )


def _llm_returning(*responses: str) -> LLMClient:
    """An LLMClient whose call_gemini returns the given responses in order."""
    client = LLMClient.__new__(LLMClient)
    client.call_gemini = AsyncMock(side_effect=list(responses))  # type: ignore[method-assign]
    return client


def _event(captured: list[dict], event_name: str) -> dict | None:
    """Return the first captured structlog event with ``event_name``, else None."""
    return next((e for e in captured if e.get("event") == event_name), None)


class TestEditorialRewriteFailsClosed:
    """A rewrite that comes back as junk is refused, not published."""

    @pytest.mark.asyncio
    async def test_unpublishable_rewritten_headline_is_refused(self) -> None:
        """The rewrite is a headline *source*, so it faces the same gate as any other.

        Fail-open here is how a masthead survives an otherwise-working rewrite: the
        model echoed the source title, and the old code shipped whatever came back.
        """
        llm = _llm_returning('{"headline": "Language Magazine", "body": "Long body."}')

        with capture_logs() as logs:
            rewrite = await run_editorial_rewrite(
                story=_canonical_story(), llm_client=llm
            )

        assert rewrite is None
        rejection = _event(logs, "editorial_rewrite_rejected")
        assert rejection is not None
        assert rejection["rejection_reason"] == "title_equals_outlet"
        assert rejection["fix_suggestion"]

    @pytest.mark.asyncio
    async def test_publishable_rewritten_headline_is_returned(self) -> None:
        """The gate must not eat good rewrites — this is the ordinary path."""
        llm = _llm_returning(
            '{"headline": "Dual-language enrollment climbs 18% this year",'
            ' "body": "Long body."}'
        )

        rewrite = await run_editorial_rewrite(story=_canonical_story(), llm_client=llm)

        assert rewrite is not None
        assert rewrite.headline == "Dual-language enrollment climbs 18% this year"


_TAGS = [
    StoryInterestTag(
        story_interest_story_id="cand-lang-001",
        story_interest_interest_id="int-lang",
        story_interest_match_depth=0,
    )
]
_SEGMENT_LOOKUP = {"int-lang": "arts"}

_SCRIPT_JSON = (
    '[{"speaker": "ALEX", "text": "What is happening in dual-language schools?"},'
    ' {"speaker": "JORDAN", "text": "Enrollment rose 18% this year."}]'
)
_VERIFY_GROUNDED = (
    '{"claims": [{"claim": "Enrollment rose 18%", "status": "SUPPORTED",'
    ' "source_evidence": "Enrollment in dual-language programs rose 18% this year."}]}'
)
_GOOD_REWRITE = (
    '{"headline": "Dual-language enrollment climbs 18% this year",'
    ' "body": "Enrollment rose sharply."}'
)
_MALFORMED_REWRITE = "sorry, I could not produce JSON for this one"


async def _write(story: CanonicalStory, llm: LLMClient, *, rewrite: bool):
    """Run the WRITE phase for one story with the segment lookup it needs."""
    return await write_phase(
        story,
        story_interest_tags=_TAGS,
        llm_client=llm,
        story_id="FIXTURE-headline",
        enable_editorial_rewrite=rewrite,
        interest_segment_lookup=_SEGMENT_LOOKUP,
    )


class TestWritePhaseFailsClosedOnHeadlines:
    """A story whose FINAL title is a masthead is dropped, never published."""

    @pytest.mark.asyncio
    async def test_masthead_title_without_rewrite_is_dropped_before_any_llm_call(
        self,
    ) -> None:
        """No rewrite to save it → reject up front, exactly like an unresolved segment.

        Rejecting before scripting also means a story that can never publish costs
        no LLM spend.
        """
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED)

        with pytest.raises(HeadlineQualityError) as excinfo:
            await _write(_canonical_story(_MASTHEAD), llm, rewrite=False)

        assert excinfo.value.rejection_reason == "title_equals_outlet"
        llm.call_gemini.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_masthead_title_survives_when_the_rewrite_is_publishable(
        self,
    ) -> None:
        """The rewrite IS the substitution — a good headline rescues a bad source title."""
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED, _GOOD_REWRITE)

        result = await _write(_canonical_story(_MASTHEAD), llm, rewrite=True)

        assert result is not None
        assert (
            result.editorial_story.canonical_title
            == "Dual-language enrollment climbs 18% this year"
        )

    @pytest.mark.asyncio
    async def test_malformed_rewrite_json_on_a_bad_title_drops_the_story(self) -> None:
        """Fail CLOSED: the old fallback ("keep the original") republished the masthead.

        This is the seam RC4 came through — the rewrite failing is exactly when the
        original title gets used, and the original title is the garbage.
        """
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED, _MALFORMED_REWRITE)

        with capture_logs() as logs:
            with pytest.raises(HeadlineQualityError):
                await _write(_canonical_story(_MASTHEAD), llm, rewrite=True)

        rejection = _event(logs, "headline_rejected")
        assert rejection is not None
        assert rejection["rejection_reason"] == "title_equals_outlet"
        assert rejection["fix_suggestion"]

    @pytest.mark.asyncio
    async def test_malformed_rewrite_json_on_a_good_title_keeps_the_source_title(
        self,
    ) -> None:
        """Fail-closed is about the TITLE, not about punishing rewrite failures.

        A publishable source headline is a legitimate substitute, so the story still
        ships — dropping it here would cost real stories for no quality gain.
        """
        llm = _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED, _MALFORMED_REWRITE)

        result = await _write(_canonical_story(), llm, rewrite=True)

        assert result is not None
        assert result.editorial_story.canonical_title == _REAL_HEADLINE


class TestPersistRejectsUnpublishableHeadlines:
    """The last line of defence: no masthead reaches ``stories.story_headline``."""

    def test_masthead_story_is_rejected_before_any_write(self) -> None:
        """``persist_digest`` is called directly by scripts + e2e fixtures, not only
        by the orchestrator, so the gate has to hold here too — and it must fire
        before the first upload, so a rejection never half-writes a story.
        """
        client = MagicMock()
        track = align_transcript_to_audio(
            digest_id="x", sentences=["A b."], audio_duration_s=2.0
        )

        with pytest.raises(HeadlineQualityError):
            persist_digest(
                supabase_client=client,
                story=_canonical_story(_MASTHEAD),
                script=DigestScript(
                    digest_story_id="cand-lang-001",
                    turns=[DialogueTurn(speaker="ALEX", text="Enrollment rose.")],
                ),
                caption_track=track,
                audio_bytes=b"FAKE",
                audio_duration_ms=2000,
                story_interest_tags=_TAGS,
                interest_segment_lookup=_SEGMENT_LOOKUP,
                story_id="FIXTURE-headline-persist",
            )

        client.table.assert_not_called()
        client.storage.from_.assert_not_called()


class TestFollowedSourceReelsAreExempt:
    """A followed creator's own title is not a scraped masthead."""

    def test_short_youtube_title_survives_the_gate(self) -> None:
        """Source reels are scarce and user-chosen, and they carry the same exemption
        at the produce gate and the poster gate. Dropping "Ep. 42" from a channel the
        user explicitly followed is a regression, not a quality win.
        """
        youtube_story = _canonical_story("Ep. 42").model_copy(
            update={
                "canonical_primary_outlet_domain": "youtube.com",
                "canonical_primary_outlet_name": "Ep. 42",
            }
        )

        reject_unpublishable_headline(youtube_story)


class TestClusterWhereEveryTitleIsBad:
    """The end of the ladder: nothing to substitute → the story is dropped."""

    @pytest.mark.asyncio
    async def test_all_bad_titles_cluster_never_publishes(self) -> None:
        """Clustering + the gate, joined: representative selection can only pick the
        best AVAILABLE title, so when every member is junk the story must die at the
        gate rather than publish under whichever junk title happened to win.
        """
        candidates = [
            _candidate(
                "gdelt-a",
                _MASTHEAD,
                "https://languagemagazine.com/d?utm_source=x",
                published_utc=_EARLIER,
            ),
            _candidate(
                "gdelt-b",
                "Breaking News",
                "https://languagemagazine.com/d",
                published_utc=_LATER,
            ),
        ]
        story = StoryClusterer().cluster_candidates(candidates)[0]
        story = story.model_copy(
            update={"canonical_body_text": "Enrollment rose 18% this year."}
        )

        with pytest.raises(HeadlineQualityError):
            await _write(
                story, _llm_returning(_SCRIPT_JSON, _VERIFY_GROUNDED), rewrite=False
            )
