r"""Unit tests for the English-only admission gate (``candidate_language``) — slice #63.

blip is an English-language product. The first SHORTLIST_ONLY run (2026-07-20,
``.agents/shortlists/2026-07-20-shortlist.json``) put **10 of 19** stories with
non-English headlines in front of the founder — German, Albanian, Chinese,
Portuguese, Polish and Azerbaijani — every one a burned reel slot. This module is
the deterministic (zero-LLM, zero-API) gate that rejects them at GDELT admission.

WHY each test matters:
  • The 2026-07-20 rows below are **verbatim fixtures** of that run's junk. They are
    replayed with ``translation_info=None`` — the PESSIMISTIC case, i.e. proving the
    title-side backstop rejects them even when GDELT's own language stamp is missing.
    In production the ``srclc:`` gate in ``_BATCH_SQL`` rejects them earlier/cheaper.
  • The accented-English cases encode the false-NEGATIVE risk: an English headline
    naming Zürich must never be dropped for owning an umlaut. A language filter that
    over-rejects silently starves the feed, which is worse than the junk it removes.
  • The no-metadata case encodes fail-OPEN: absence of evidence is not evidence of
    foreignness, so we admit — but the caller must be able to SEE that it happened
    (Rule 12), which is why the verdict carries ``language_evidence``.

    >>> pytest tests/agents/ingestion/test_candidate_language.py -v
"""

from __future__ import annotations

import pytest

from agents.ingestion.candidate_language import (
    LANGUAGE_EVIDENCE_ENGLISH_TERMS,
    LANGUAGE_EVIDENCE_GDELT_STAMP,
    LANGUAGE_EVIDENCE_NONE,
    REJECT_FOREIGN_DIACRITIC_DENSITY,
    REJECT_FOREIGN_FUNCTION_WORDS,
    REJECT_NON_ENGLISH_SOURCE_LANGUAGE,
    REJECT_NON_LATIN_SCRIPT,
    build_language_filter_sql,
    evaluate_language_admission,
    extract_source_language_code,
)

# Reason: verbatim (title, source_language, outlet) triples from the 2026-07-20
# shortlist — the acceptance fixtures for criterion 3. Titles are kept EXACTLY as
# GDELT delivered them (HTML-entity-encoded), because the entity encoding is part of
# what the gate must cope with; decoding them here would test a different input.
SHORTLIST_2026_07_20_NON_ENGLISH: list[tuple[str, str, str]] = [
    (
        "&#x767E;&#x5EA6;&#x6606;&#x4ED1;&#x82AF;M100&#x9996;&#x6B21;&#x5C55;&#x51FA;"
        "&#xFF1A;&#x5168;&#x56FD;&#x4EA7;&#x5BF9;&#x6807;NVIDIA H20&#xFF01;",
        "chinese",
        "163.com",
    ),
    (
        "Apple kompania m&#xEB; e vlefshme n&#xEB; bot&#xEB;, l&#xEB; pas Nvidia",
        "albanian",
        "telegrafi.com",
    ),
    (
        "Rally dos chips de IA entra em xeque com temor sobre capex e valuation",
        "portuguese",
        "infomoney.com.br",
    ),
    (
        "NVIDIA GeForce RTX 5000 - Popularne, diagnostyczne programy "
        "zacz&#x119;&#x142;y raportowa&#x107; informacje o temperaturach hot spot",
        "polish",
        "purepc.pl",
    ),
    (
        "&#x65E5;&#x672C;&#x6A5F;&#x5668;&#x4EBA;&#x8207;&#x88FD;&#x9020;&#x696D;"
        "&#x9818;&#x5C0E;&#x8005;&#x904B;&#x7528; NVIDIA Cosmos &#x63A8;&#x9032;"
        "&#x7269;&#x7406; AI &#x524D;&#x6CBF;",
        "chinese",
        "ithome.com.tw",
    ),
    (
        "&#x963F;&#x91CC;&#x5DF4;&#x5DF4;&#x958B;&#x6E90; T-Head SAIL&#xFF0C;"
        "&#x6311;&#x6230; NVIDIA CUDA &#x751F;&#x614B;&#x7CFB;",
        "chinese",
        "technews.tw",
    ),
    (
        "Holy shit! Sonntag-Schreck f&#xFC;r Nvidia und SpaceX: Alibaba kontert GPT",
        "german",
        "finanznachrichten.de",
    ),
    (
        "Valve warnt: PC-Hardware k&#xF6;nnte noch teurer werden",
        "german",
        "gamezone.de",
    ),
    (
        "BMT-d&#x259; Az&#x259;rbaycan&#x131;n iqlim v&#x259; &#x15F;&#x259;h&#x259;"
        "rsalma t&#x259;cr&#xFC;b&#x259;sind&#x259;n dan&#x131;&#x15F;&#x131;l&#x131;b",
        "azerbaijani",
        "xalqcebhesi.az",
    ),
    (
        "Midea PortaSplit kaufen: Seite zeigt, wo das Klimager&#xE4;t aktuell "
        "verf&#xFC;gbar ist",
        "german",
        "sol.de",
    ),
]

# Reason: the English rows from the SAME shortlist run. They are the regression floor —
# whatever the gate does to the junk above, it must keep every one of these.
SHORTLIST_2026_07_20_ENGLISH: list[tuple[str, str]] = [
    (
        "TSMC is accelerating Arizona fab buildout to capitalize on AI demand: CFO",
        "cnbc.com",
    ),
    (
        "Meta Facebook Service Restored | Nvidia CEO Jensen Huang Jacket Auction",
        "bhaskar.com",
    ),
    (
        "Nvidia: AI Infrastructure Hitting Physical And Financial Limits (NASDAQ:NVDA)",
        "seekingalpha.com",
    ),
    (
        "Fujitsu Ltd: Fujitsu to explore physical AI development and implementation "
        "across industries with FANUC, Yaskawa Electric, and Kawasaki Heavy "
        "Industries integrating NVIDIA technology",
        "finanznachrichten.de",
    ),
    (
        "Fish And Chips Spots Worth Knowing About At The Jersey Shore",
        "1057thehawk.com",
    ),
    (
        "AGIBOT Unveils Four New Products at WAIC 2026, Showcasing Embodied AI in "
        "Real-World Operations",
        "thailand-business-news.com",
    ),
    (
        "PlayStation 6 Release Date Leak: Sony Patent Reveals Crucial Next Gen "
        "Hardware Cooling Upgrade",
        "ibtimes.co.uk",
    ),
    (
        "The Sound of Depression: Translating Brain Activity Into Language",
        "haaretz.com",
    ),
]


class TestHappyPathAdmitsEnglishRejectsForeign:
    """Criterion 1: an English candidate admits; German / Chinese ones do not."""

    def test_plain_english_headline_admits(self) -> None:
        """WHY: the gate's whole cost is borne by English stories — if the happy path
        ever rejects, the feed starves and the junk it removed is irrelevant."""
        verdict = evaluate_language_admission(
            "Fed holds rates steady as inflation cools", None
        )
        assert verdict.is_admitted
        assert verdict.rejection_reason is None

    def test_german_headline_is_rejected(self) -> None:
        """WHY: German was the most frequent junk language in the 07-20 shortlist
        (3 of 19 slots). Latin script, so ONLY the function-word signal can catch it."""
        verdict = evaluate_language_admission(
            "Valve warnt: PC-Hardware k&#xF6;nnte noch teurer werden", None
        )
        assert not verdict.is_admitted
        assert verdict.rejection_reason == REJECT_FOREIGN_FUNCTION_WORDS

    def test_polish_headline_is_rejected_on_accent_density(self) -> None:
        """WHY: a Polish HARDWARE headline carries no Polish function words at all, so
        only the accented-word density rule can catch it. That same rule must not be
        what catches 'Zürich' — the two cases separate on distinct-accented-word count
        plus English evidence, and both directions are asserted (see the edge class)."""
        verdict = evaluate_language_admission(
            "Popularne, diagnostyczne programy zacz&#x119;&#x142;y raportowa&#x107; "
            "informacje o temperaturach hot spot",
            None,
        )
        assert not verdict.is_admitted
        assert verdict.rejection_reason == REJECT_FOREIGN_DIACRITIC_DENSITY

    def test_chinese_headline_is_rejected(self) -> None:
        """WHY: CJK is unambiguous — a title outside Latin script can never be an
        English story, so this rejection must be definitive, not heuristic."""
        verdict = evaluate_language_admission(
            "&#x963F;&#x91CC;&#x5DF4;&#x5DF4;&#x958B;&#x6E90; NVIDIA CUDA", None
        )
        assert not verdict.is_admitted
        assert verdict.rejection_reason == REJECT_NON_LATIN_SCRIPT

    def test_literal_unicode_title_rejected_without_entity_encoding(self) -> None:
        """WHY: GDELT delivers some titles entity-encoded and some as literal UTF-8.
        The gate must not depend on which encoding arrived."""
        verdict = evaluate_language_admission("阿里巴巴开源 NVIDIA CUDA", None)
        assert not verdict.is_admitted
        assert verdict.rejection_reason == REJECT_NON_LATIN_SCRIPT

    def test_gdelt_source_language_stamp_rejects_before_any_heuristic(self) -> None:
        """WHY: GDELT's own TranslationInfo is authoritative — a machine-translated
        doc is junk for us even when its title reads as fluent English (the albeu.com
        row: an Albanian domestic story whose title arrived translated)."""
        verdict = evaluate_language_admission(
            "He called them disgusting beings, Lutfi Dervishi: The canvasser pokes "
            "his nose into the private lives of Albanians",
            "srclc:sqi;eng:Moses 2.1.1 / MosesCore Europarl sq-en",
        )
        assert not verdict.is_admitted
        assert verdict.rejection_reason == REJECT_NON_ENGLISH_SOURCE_LANGUAGE
        assert verdict.source_language_code == "sqi"
        assert verdict.language_evidence == LANGUAGE_EVIDENCE_GDELT_STAMP


class TestEdgeAccentedEnglishStillAdmits:
    """Criterion 2: accents are NOT foreignness — an English title keeps its umlauts.

    WHY: this is the expensive failure. Rejecting on any non-ASCII character would
    drop 'Zürich', 'São Paulo', 'Beyoncé' and 'café' stories — real English coverage —
    and the loss would be invisible (a smaller pool, no error). Every rule below is
    therefore thresholded or gated by positive English evidence.
    """

    def test_accented_proper_noun_admits(self) -> None:
        """A single accented proper noun in an otherwise English headline admits."""
        verdict = evaluate_language_admission(
            "UBS in Z&#xFC;rich reports a record quarterly profit", None
        )
        assert verdict.is_admitted, verdict.rejection_reason

    def test_accented_proper_noun_admits_without_english_function_words(self) -> None:
        """Headline-ese drops function words ('Zürich Bank UBS Q3 Profit Record'), so
        the diacritic threshold — not the English signal — must carry this case."""
        verdict = evaluate_language_admission(
            "Z&#xFC;rich Bank UBS Q3 Profit Record", None
        )
        assert verdict.is_admitted, verdict.rejection_reason

    def test_two_accented_proper_nouns_admit_when_english_terms_present(self) -> None:
        """WHY: the diacritic-density rule must yield to positive English evidence —
        two foreign place names in a clearly English sentence is still English."""
        verdict = evaluate_language_admission(
            "Z&#xFC;rich and Gen&#xE8;ve sign a climate deal with Bern", None
        )
        assert verdict.is_admitted, verdict.rejection_reason

    def test_literal_accented_characters_admit(self) -> None:
        """The same guarantee for literal UTF-8 accents (no entity encoding)."""
        verdict = evaluate_language_admission(
            "São Paulo airport reopens after the storm", None
        )
        assert verdict.is_admitted, verdict.rejection_reason

    def test_two_accented_names_admit_with_no_english_function_words(self) -> None:
        """WHY: 'Beyoncé, Céline win Grammys' is English with TWO accented words and
        no function words — the density rule would have dropped it. Accents confined
        to CAPITALIZED (proper-noun) positions are names, not foreign prose, so only
        lowercase accented words count as evidence."""
        verdict = evaluate_language_admission("Beyonc&#xE9;, C&#xE9;line win Grammys")
        assert verdict.is_admitted, verdict.rejection_reason

    def test_lowercase_accented_prose_still_rejects(self) -> None:
        """The other side of that discriminator: the SAME two-accented-word count in
        lowercase (mid-sentence) positions is foreign prose and must still reject."""
        verdict = evaluate_language_admission(
            "programy zacz&#x119;&#x142;y raportowa&#x107;"
        )
        assert not verdict.is_admitted
        assert verdict.rejection_reason == REJECT_FOREIGN_DIACRITIC_DENSITY

    def test_english_stamped_by_gdelt_admits(self) -> None:
        """An explicit srclc:eng stamp admits and is recorded as positive evidence."""
        verdict = evaluate_language_admission(
            "Sony patent reveals PlayStation cooling upgrade", "srclc:eng;eng:passthru"
        )
        assert verdict.is_admitted
        assert verdict.language_evidence == LANGUAGE_EVIDENCE_GDELT_STAMP
        assert verdict.source_language_code == "eng"


class TestShortlist20260720Fixtures:
    """Criterion 3: every non-English row of the 2026-07-20 shortlist is rejected.

    Replayed with NO GDELT language stamp on purpose: this proves the title-side
    backstop alone is sufficient, so the gate does not silently depend on a field we
    could not verify offline (zero live API calls).
    """

    @pytest.mark.parametrize(
        ("title", "language", "outlet"),
        SHORTLIST_2026_07_20_NON_ENGLISH,
        ids=[
            f"{lang}-{outlet}" for _, lang, outlet in SHORTLIST_2026_07_20_NON_ENGLISH
        ],
    )
    def test_non_english_shortlist_row_is_rejected(
        self, title: str, language: str, outlet: str
    ) -> None:
        """WHY: these ten rows are ~half the founder's 07-20 shortlist. Each one is a
        reel slot that produced nothing a reader of an English product can use."""
        verdict = evaluate_language_admission(title, None)
        assert not verdict.is_admitted, f"{language} row admitted: {title}"
        assert verdict.rejection_reason is not None

    def test_all_seven_plus_non_english_rows_rejected(self) -> None:
        """The count is the acceptance number the issue names (>= 7); assert it as a
        number so a partial regression (say, only CJK still caught) fails loud."""
        rejected = [
            title
            for title, _, _ in SHORTLIST_2026_07_20_NON_ENGLISH
            if not evaluate_language_admission(title, None).is_admitted
        ]
        assert len(rejected) == len(SHORTLIST_2026_07_20_NON_ENGLISH) == 10

    @pytest.mark.parametrize(
        ("title", "outlet"),
        SHORTLIST_2026_07_20_ENGLISH,
        ids=[outlet for _, outlet in SHORTLIST_2026_07_20_ENGLISH],
    )
    def test_english_shortlist_row_still_admits(self, title: str, outlet: str) -> None:
        """WHY: the same run's English rows are the false-negative regression floor."""
        verdict = evaluate_language_admission(title, None)
        assert verdict.is_admitted, f"{outlet} row rejected: {verdict.rejection_reason}"

    def test_english_title_on_a_german_outlet_admits(self) -> None:
        """WHY: the gate filters LANGUAGE, not geography. finanznachrichten.de shipped
        BOTH a German headline (rejected above) and this English press release in the
        same run — a domain/TLD filter would have been wrong on one of them."""
        english_on_de_outlet = SHORTLIST_2026_07_20_ENGLISH[3][0]
        assert evaluate_language_admission(english_on_de_outlet, None).is_admitted


class TestBoundaryFailsOpenVisibly:
    """Criterion 4: no language metadata → ADMIT, and say so (never silently)."""

    def test_no_metadata_and_no_title_signal_admits(self) -> None:
        """WHY: absence of evidence is not evidence of foreignness. A headline-ese
        title with no function words either way must pass — fail-open, not fail-shut."""
        verdict = evaluate_language_admission(
            "PlayStation 6 Release Date Leak: Sony Patent Reveals Cooling Upgrade",
            None,
        )
        assert verdict.is_admitted
        assert verdict.language_evidence == LANGUAGE_EVIDENCE_NONE
        assert verdict.source_language_code is None

    def test_fail_open_admission_is_distinguishable_from_a_confident_one(self) -> None:
        """WHY (Rule 12): the caller logs on this field. If a blind admission looked
        identical to an evidenced one, the fail-open would be invisible in the run
        log and nobody could ever measure how much of the feed is unverified."""
        blind = evaluate_language_admission("Nvidia CEO Jacket Auction", None)
        evidenced = evaluate_language_admission(
            "The sound of depression: translating brain activity into language", None
        )
        assert blind.language_evidence == LANGUAGE_EVIDENCE_NONE
        assert evidenced.language_evidence == LANGUAGE_EVIDENCE_ENGLISH_TERMS
        assert blind.is_admitted and evidenced.is_admitted

    def test_unparseable_translation_info_falls_back_to_title_signals(self) -> None:
        """A TranslationInfo we cannot parse is metadata-ABSENT, not metadata-foreign."""
        verdict = evaluate_language_admission(
            "Fed holds rates steady as inflation cools", "garbage-without-srclc"
        )
        assert verdict.is_admitted
        assert verdict.source_language_code is None

    def test_empty_title_admits_rather_than_guessing(self) -> None:
        """An empty/whitespace title carries no language signal — admit and let the
        existing 'row missing required fields' guard drop it for the real reason."""
        verdict = evaluate_language_admission("   ", None)
        assert verdict.is_admitted
        assert verdict.language_evidence == LANGUAGE_EVIDENCE_NONE

    def test_numbers_and_punctuation_only_title_admits(self) -> None:
        """No letters at all → no evidence → fail open (never a crash, never a guess)."""
        assert evaluate_language_admission("2026: 10-4 (+3.5%)", None).is_admitted


class TestSourceLanguageExtraction:
    """The srclc parser — the twin of the SQL's REGEXP_EXTRACT."""

    def test_extracts_iso_code_from_gdelt_translation_info(self) -> None:
        assert extract_source_language_code("srclc:deu;eng:Moses 2.1.1") == "deu"

    def test_case_and_space_tolerant(self) -> None:
        """GDELT has emitted both 'srclc:fra' and 'SRCLC: FRA' shapes over time."""
        assert extract_source_language_code("SRCLC: FRA ; ENG:GT") == "fra"

    def test_missing_or_empty_returns_none(self) -> None:
        """None means 'no stamp' — the caller fails OPEN on it, so it must never be
        conflated with a parsed code."""
        assert extract_source_language_code(None) is None
        assert extract_source_language_code("") is None
        assert extract_source_language_code("eng:Moses only") is None


class TestSqlTwin:
    """The SQL half must evaluate the SAME srclc rule as the Python half.

    WHY: BigQuery cannot run offline (zero live API calls), so drift between the
    admission SQL and this module can only be caught structurally. The SQL predicate
    is BUILT here, so there is exactly one place the rule is written.
    """

    def test_builder_emits_the_srclc_predicate(self) -> None:
        assert build_language_filter_sql() == (
            "AND IFNULL(REGEXP_EXTRACT(LOWER(TranslationInfo), "
            "r'srclc:\\s*([a-z]{2,3})'), 'eng') IN ('en', 'eng')"
        )

    def test_sql_default_matches_python_fail_open(self) -> None:
        """The SQL defaults a missing stamp to 'eng' (admit). That IFNULL default is
        the fail-open contract — if it ever changed to a reject, non-stamped English
        rows would vanish from ingestion with no error anywhere."""
        assert "'eng') IN (" in build_language_filter_sql()
        assert evaluate_language_admission("Fed holds rates steady", None).is_admitted

    def test_sql_accepts_every_code_python_accepts(self) -> None:
        """WHY: the SQL runs FIRST and its rejections are unrecoverable. If it accepted
        a narrower code set than Python (e.g. only 'eng' while Python also allows the
        639-1 'en'), English rows would be dropped before the Python half ever saw
        them — an over-rejection with no log line anywhere."""
        sql = build_language_filter_sql()
        for code in ("en", "eng"):
            assert f"'{code}'" in sql
            assert evaluate_language_admission(
                "Some headline", f"srclc:{code};eng:passthru"
            ).is_admitted

    def test_sql_regex_and_python_regex_extract_the_same_code(self) -> None:
        """The SQL's capture group and the Python parser must agree on the code."""
        sql = build_language_filter_sql()
        assert "srclc:" in sql and "[a-z]{2,3}" in sql
        assert extract_source_language_code("srclc:zho;eng:GT") == "zho"
