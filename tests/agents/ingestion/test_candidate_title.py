r"""Unit tests for title hygiene at GDELT admission (``candidate_title``) — slice #71.

The 2026-07-25 SHORTLIST_ONLY audit (``docs/ops/m1-live-audit-2026-07-25.md``) found the
masthead/fragment gate passing titles that are informative but DIRTY: 10 rows carrying
raw HTML entities into ``canonical_title`` and 5 rows carrying a trailing outlet suffix.
The shortlist is a founder-review surface and dedup's publishable-title vote reads the
same strings, so dirt costs twice.

WHY each test matters:
  • The 2026-07-25 rows are **verbatim fixtures** of that artifact
    (``.agents/shortlists/2026-07-25-shortlist.pre70.json``) — the acceptance evidence
    for the slice. Cleaning them by hand in the fixture would test a different input.
  • The fragment guard is the expensive direction: this gate has NO fallback title
    downstream (``reject_unpublishable_headline`` drops the story), so a suffix strip
    that leaves a 2-word stub costs a reel. Every strip must leave a title the SAME
    classifier still calls publishable — which is why row 54's suffix survives.
  • Decoding happens EXACTLY once. Running ``html.unescape`` twice turns a
    double-escaped literal (``&amp;#x27;``) into a character the publisher never wrote,
    so composition with #63 (which decodes internally, for detection only) is tested
    at the adapter seam, not assumed.

    >>> pytest tests/agents/ingestion/test_candidate_title.py -v
"""

from __future__ import annotations

import pytest
from structlog.testing import capture_logs

from agents.ingestion.adapters.gdelt_bigquery import GdeltBigQueryAdapter
from agents.ingestion.candidate_title import clean_candidate_title

# Reason: verbatim (title, outlet_domain, expected_clean_title) triples from the
# 2026-07-25 shortlist. Row numbers are the audit's. Titles are kept EXACTLY as GDELT
# delivered them — entity-encoded, suffix and all.
SHORTLIST_2026_07_25_DIRTY: list[tuple[str, str, str]] = [
    # Row 7 — ": Bollywood News" suffix the domain does not spell out.
    (
        'Manoj Muntashir calls Adipurush his "biggest mistake", says he is '
        '"ashamed" of controversial dialogues : Bollywood News',
        "bollywoodhungama.com",
        'Manoj Muntashir calls Adipurush his "biggest mistake", says he is '
        '"ashamed" of controversial dialogues',
    ),
    # Row 19 — "- Ipswich Town News" on twtd.co.uk; the leading "Maeda:" colon must
    # survive (it is part of the headline, not a masthead seam).
    (
        "Maeda: Happy to Fulfil Premier League Dream With Town - Ipswich Town News",
        "twtd.co.uk",
        "Maeda: Happy to Fulfil Premier League Dream With Town",
    ),
    # Row 21 — entity-encoded en-dash AND an outlet suffix the domain DOES spell out.
    (
        "UN debate puts spotlight on critical minerals governance &#x2013; Channel Africa",
        "channelafrica.co.za",
        "UN debate puts spotlight on critical minerals governance",
    ),
    # Row 42 — "| Alice 107.7" matches alice1077.com only after punctuation is folded.
    (
        "Shakira and Burna Boy's Dai Dai Hits New Streaming High After World Cup "
        "Final | Alice 107.7",
        "alice1077.com",
        "Shakira and Burna Boy's Dai Dai Hits New Streaming High After World Cup Final",
    ),
    # Row 36 — the English entity row: a non-breaking space fused two words together.
    (
        "Abhishek Bachchan and Aishwarya Rai&#xA0;spotted clicking pictures with fan "
        "during getaway in NYC, fans call them 'Bollywood royalty'",
        "moneycontrol.com",
        "Abhishek Bachchan and Aishwarya Rai spotted clicking pictures with fan "
        "during getaway in NYC, fans call them 'Bollywood royalty'",
    ),
    # Row 9 — full entity soup. #63 rejects this row at admission (non-Latin script);
    # it is kept here because the DECODE must not depend on the language of the title.
    (
        "&#x7B2C;&#x4E8C;&#x8258;&#x56FD;&#x4EA7; 18 &#x4E07;&#x65B9;&#x8D85;&#x5927;"
        "&#x578B; LNG &#x8239;&#x4EA4;&#x4ED8;&#xFF0C;&#x53EF;&#x8FD0;&#x8F93;&#x96F6;"
        "&#x4E0B; 163&#x2103; &#x4F4E;&#x6E29;&#x6DB2;&#x5316;&#x5929;&#x7136;&#x6C14;",
        "sina.com.cn",
        "第二艘国产 18 万方超大型 LNG 船交付，可运输零下 163℃ 低温液化天然气",
    ),
    # Row 54 — the fragment guard in real data: stripping "- CFi.CN 中财网" would leave
    # a 2-whitespace-word stub that reject_unpublishable_headline drops, so the ORIGINAL
    # (decoded) title is kept. Never trade dirty for unpublishable.
    (
        "&#x6CB3;&#x5357;GDP&#x9886;&#x8DD1;&#x5168;&#x56FD;5%&#x589E;&#x901F; "
        "&#x9AD8;&#x6280;&#x672F;&#x5236;&#x9020;&#x4E1A;&#x8FCE;&#x91CD;&#x5927;"
        "&#x6295;&#x8D44;&#x673A;&#x9047;- CFi.CN &#x4E2D;&#x8D22;&#x7F51;",
        "cfi.net.cn",
        "河南GDP领跑全国5%增速 高技术制造业迎重大投资机遇- CFi.CN 中财网",
    ),
]


class TestShortlistRegression:
    """The 2026-07-25 dirty rows, replayed."""

    @pytest.mark.parametrize(
        ("raw_title", "outlet_domain", "expected_title"),
        SHORTLIST_2026_07_25_DIRTY,
        ids=[
            "row7_suffix",
            "row19_suffix",
            "row21_suffix",
            "row42_suffix",
            "row36_entity",
            "row9_entity",
            "row54_fragment_guard",
        ],
    )
    def test_dirty_shortlist_row_is_cleaned(
        self, raw_title: str, outlet_domain: str, expected_title: str
    ) -> None:
        """Each audited row cleans to the title the founder should have seen."""
        assert (
            clean_candidate_title(raw_title, outlet_domain=outlet_domain)
            == expected_title
        )

    @pytest.mark.parametrize(
        ("raw_title", "outlet_domain", "expected_title"),
        SHORTLIST_2026_07_25_DIRTY,
        ids=["row7", "row19", "row21", "row42", "row36", "row9", "row54"],
    )
    def test_cleaning_is_idempotent(
        self, raw_title: str, outlet_domain: str, expected_title: str
    ) -> None:
        """Re-cleaning a cleaned title changes nothing.

        WHY: the pipeline must be safe against a second pass (a re-ingest, a replay,
        a caller that cleans defensively) — a second decode would invent characters
        and a second strip could eat real words.
        """
        once = clean_candidate_title(raw_title, outlet_domain=outlet_domain)
        assert clean_candidate_title(once, outlet_domain=outlet_domain) == once


class TestEntityDecoding:
    """html.unescape — exactly once, never zero, never twice."""

    def test_named_and_numeric_entities_decode(self) -> None:
        """Both entity spellings GDELT emits are decoded."""
        assert (
            clean_candidate_title(
                "Fed &amp; Treasury clash over &#x2018;soft landing&#x2019;"
            )
            == "Fed & Treasury clash over ‘soft landing’"
        )

    def test_double_escaped_entity_decodes_exactly_one_level(self) -> None:
        """``&amp;#x27;`` decodes to the LITERAL ``&#x27;``, not to an apostrophe.

        WHY: the publisher wrote a visible ``&#x27;`` on the page. Decoding twice would
        silently rewrite the headline, which is why this module owns the ONLY unescape
        on the admission path and #63's detection-side decode reads the raw title.
        """
        assert (
            clean_candidate_title("Apple &amp;#x27;wins&amp;#x27; the appeal in court")
            == "Apple &#x27;wins&#x27; the appeal in court"
        )

    def test_non_breaking_space_becomes_a_real_space(self) -> None:
        """``&#xA0;`` is whitespace, so words either side must not fuse.

        The slice's happy path (decode AND strip in one pass), written at a realistic
        headline length — see the next test for why the issue's own ``A B`` spelling
        cannot hold.
        """
        assert (
            clean_candidate_title(
                "Rai&#xA0;spotted in NYC : Bollywood News",
                outlet_domain="bollywoodhungama.com",
            )
            == "Rai spotted in NYC"
        )

    def test_two_word_remainder_loses_to_the_fragment_guard(self) -> None:
        """``A&#xA0;B : Bollywood News`` keeps its suffix — AC1 vs AC2, resolved.

        The issue spells the happy path as ``A&#xA0;B : Bollywood News`` -> ``A B``, but
        ``A B`` is a two-word fragment, and the issue ALSO requires that a strip never
        create one. The guard wins: a fragment is dropped outright at persist, so the
        dirty-but-publishable title is strictly the better of the two outcomes. The
        decode still happens.
        """
        assert (
            clean_candidate_title(
                "A&#xA0;B : Bollywood News", outlet_domain="bollywoodhungama.com"
            )
            == "A B : Bollywood News"
        )

    def test_clean_title_passes_through_unchanged(self) -> None:
        """A title with nothing wrong is returned byte-identical."""
        title = "Fed holds rates steady as inflation cools"
        assert clean_candidate_title(title, outlet_domain="reuters.com") == title


class TestOutletSuffixStripping:
    """The suffix strip and the fragment guard that bounds it."""

    def test_suffix_strip_never_creates_a_fragment(self) -> None:
        """A strip that would leave a 2-word stub keeps the ORIGINAL title.

        WHY: there is no fallback title downstream — an unpublishable one is dropped
        (PRD decision 6), so a dirty-but-publishable headline beats a clean fragment.
        """
        assert (
            clean_candidate_title(
                "Town Wins - Ipswich Town News", outlet_domain="twtd.co.uk"
            )
            == "Town Wins - Ipswich Town News"
        )

    def test_remainder_equal_to_the_outlet_keeps_the_original(self) -> None:
        """The masthead half of the same classifier also guards the strip."""
        assert (
            clean_candidate_title(
                "Channel Africa – Channel Africa", outlet_domain="channelafrica.co.za"
            )
            == "Channel Africa – Channel Africa"
        )

    def test_trailing_clause_of_a_real_headline_survives(self) -> None:
        """A dash before ordinary prose is a headline, not a masthead seam."""
        title = "Trump and Xi meet in Seoul - what it means for the trade war"
        assert clean_candidate_title(title, outlet_domain="cnn.com") == title

    def test_leading_attribution_colon_survives(self) -> None:
        """``Report: …`` is the most common headline shape there is."""
        title = "Report: Fed holds rates steady through the spring"
        assert clean_candidate_title(title, outlet_domain="reuters.com") == title

    def test_hyphenated_words_are_not_separators(self) -> None:
        """A separator needs whitespace after it; ``state-of-the-art`` has none."""
        title = "State-of-the-art chip fab opens in Ohio"
        assert clean_candidate_title(title, outlet_domain="wsj.com") == title

    def test_fused_second_headline_after_the_outlet_name_is_dropped(self) -> None:
        """``… - FINCHANNELNASA Watchdogs Raise …`` (audit comment, post-#70 row 33).

        WHY: two headlines concatenated with the masthead fused into the seam is the
        same defect as a trailing suffix — the tail STARTS with the outlet's own name —
        so the same outlet-key rule must catch it rather than needing a second gate.
        """
        assert clean_candidate_title(
            "NASA Watchdogs Warn Starship Faces Major Hurdles as SpaceX Pushes Toward "
            "Moon and Mars - FINCHANNELNASA Watchdogs Raise New Concerns Over SpaceX "
            "Starship as Elon Musk Pursues Moon and Mars",
            outlet_domain="finchannel.com",
        ) == (
            "NASA Watchdogs Warn Starship Faces Major Hurdles as SpaceX Pushes "
            "Toward Moon and Mars"
        )

    def test_generic_domain_labels_never_match_a_prefix(self) -> None:
        """``news.yam.md`` must not let any tail starting with "News…" be stripped.

        WHY: the prefix rule is the risky half. A two/three-letter registry label
        ("co", "net", "news") would match ordinary prose and silently truncate real
        headlines, so those labels are excluded from outlet matching entirely.
        """
        title = "Moldova signs the grid deal - Newsroom staff report the details"
        assert clean_candidate_title(title, outlet_domain="news.yam.md") == title

    def test_short_outlet_key_matches_exactly_but_never_as_a_prefix(self) -> None:
        """A 3-letter outlet name may END a title but must not swallow prose.

        WHY: "art.com" would otherwise turn "… - Art of the deal, explained" into a
        prefix hit and eat the clause. Exact matches at that length are still the right
        call — "… - CNN" is a masthead, not a sentence — so only the PREFIX rule is
        length-gated.
        """
        prose = "Ohio fab breaks ground - Art of the deal, explained"
        assert clean_candidate_title(prose, outlet_domain="art.com") == prose
        assert (
            clean_candidate_title(
                "Fed holds rates steady as inflation cools - CNN",
                outlet_domain="cnn.com",
            )
            == "Fed holds rates steady as inflation cools"
        )

    def test_missing_outlet_metadata_still_decodes(self) -> None:
        """With no domain to match, entity decoding must still happen."""
        assert (
            clean_candidate_title("Rai&#xA0;spotted in NYC with fans")
            == "Rai spotted in NYC with fans"
        )

    def test_whitespace_only_title_keeps_the_original(self) -> None:
        """Cleaning never manufactures an empty title out of a non-empty one."""
        assert clean_candidate_title("&#xA0;") == "&#xA0;"


class TestAdapterSeamComposesWithLanguageGate:
    """#71 (clean the stored title) must not move #63 (admit/reject) by an inch."""

    def test_admitted_row_carries_the_cleaned_title(self, make_bq_row) -> None:
        """The wiring, not just the pure function: row 19 replayed through the adapter.

        The rewrite is also logged in aggregate — a gate that silently edits what the
        founder reviews is exactly the kind of invisible filter Rule 12 forbids.
        """
        adapter = GdeltBigQueryAdapter()
        with capture_logs() as logs:
            candidates = adapter._rows_to_candidates(
                [
                    make_bq_row(
                        "https://twtd.co.uk/news/1",
                        "Maeda: Happy to Fulfil Premier League Dream With Town - "
                        "Ipswich Town News",
                        "twtd.co.uk",
                    )
                ],
                stamp_interest=True,
            )
        cleaned_logs = [
            entry
            for entry in logs
            if entry["event"] == "gdelt_candidate_titles_cleaned"
        ]
        assert len(cleaned_logs) == 1
        assert cleaned_logs[0]["titles_cleaned"] == 1
        assert len(candidates) == 1
        assert (
            candidates[0].candidate_title
            == "Maeda: Happy to Fulfil Premier League Dream With Town"
        )

    def test_language_gate_still_reads_the_raw_title(self, make_bq_row) -> None:
        """A row whose only non-Latin evidence sits in the outlet suffix stays REJECTED.

        WHY: if cleaning ran BEFORE the gate it would strip that suffix and smuggle the
        row past #63. Ordering is the contract — gate on raw, then clean.
        """
        adapter = GdeltBigQueryAdapter()
        candidates = adapter._rows_to_candidates(
            [
                make_bq_row(
                    "https://cfi.net.cn/a",
                    "Zhengzhou factory output rises sharply - CFi.CN &#x4E2D;&#x8D22;&#x7F51;",
                    "cfi.net.cn",
                )
            ],
            stamp_interest=True,
        )
        assert candidates == []

    def test_gate_verdict_is_unchanged_for_an_entity_encoded_foreign_title(
        self, make_bq_row
    ) -> None:
        """#63's fixtures keep their verdict: entity-encoded CJK is still rejected."""
        adapter = GdeltBigQueryAdapter()
        candidates = adapter._rows_to_candidates(
            [
                make_bq_row(
                    "https://sina.com.cn/a",
                    SHORTLIST_2026_07_25_DIRTY[5][0],
                    "sina.com.cn",
                )
            ],
            stamp_interest=True,
        )
        assert candidates == []
