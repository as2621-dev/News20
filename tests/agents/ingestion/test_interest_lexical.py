r"""Unit tests for the lexical relevance key (``interest_lexical``) — RC3, slice #50.

The lexical half of the two-key relevance lock (PRD decision 5). These are the
behavioural acceptance tests for the five criteria and the four RC3 false
positives, run against the pure ``derive_anchor_specs`` + ``evaluate_anchor_match``
functions. Those functions ARE the specification the ``_BATCH_SQL`` admission gate
is built to satisfy (its twin) — the SQL cannot execute offline (zero-cost
constraint), so the wiring that proves the SQL evaluates the same predicate and
consumes the same anchors lives in ``test_gdelt_bigquery_adapter.py``.

WHY these matter: RC3 is why the founder's 07-07 and 07-19 AI slots were nearly
all junk — single-word bag-of-words matching admitted venison ("Foundation"),
Hawaii funding ("Federal") and an Indonesia port ("India") into interests they had
nothing to do with. Each test below encodes a distinct failure mode; a regression
silently reintroduces false positives into the slots the user cares most about.

    >>> pytest tests/agents/ingestion/test_interest_lexical.py -v
"""

from __future__ import annotations

import pytest

from agents.ingestion.interest_lexical import (
    _BANNED_STANDALONE_ANCHORS,
    derive_anchor_specs,
    evaluate_anchor_match,
)


def _admits(query: str, title: str, entities: str = "") -> bool:
    """Does ``query``'s lexical key admit a story with this title + entity haystack?

    Mirrors the SQL twin: ``hay = LOWER(title + ' ' + persons/orgs/locations)`` and
    ``title_hay = LOWER(title)``. Pass ``entities`` to place an anchor ONLY in the
    entity haystack (not the title) — the case the ``requires_title`` rule gates.
    """
    specs = derive_anchor_specs(query).specs
    title_hay = title.lower()
    hay = f"{title} {entities}".lower()
    return evaluate_anchor_match(title_hay, hay, specs)


class TestCriterion1GenuineMultiPhraseAnchor:
    """A genuine multi-phrase anchor match admits (and, via the stamp, tags right)."""

    def test_multi_phrase_anchor_admits_on_phrase_hit(self) -> None:
        """WHY: the happy path — a story genuinely about the interest, where an anchor
        PHRASE appears intact, must still admit. Precision must not become rejection."""
        assert _admits(
            "artificial intelligence, machine learning, OpenAI",
            "OpenAI advances artificial intelligence safety research",
        )

    def test_any_one_of_several_phrases_suffices(self) -> None:
        """WHY: anchors are OR-ed — a story hitting just one of the interest's phrases
        is on-topic. Here only 'machine learning' appears, and it admits."""
        assert _admits(
            "artificial intelligence, machine learning, OpenAI",
            "New machine learning benchmark released today",
        )

    def test_phrase_not_matched_as_bag_of_words(self) -> None:
        """WHY: the core RC3 fix — 'artificial intelligence' must match as a contiguous
        PHRASE, not as 'artificial' OR 'intelligence' anywhere. This story contains
        both words scattered and unrelated; it must be rejected."""
        assert not _admits(
            "artificial intelligence",
            "An intelligence agency funds an artificial reef project",
        )


class TestCriterion2BannedStandaloneGenerics:
    """Each banned standalone generic can never admit alone (table-driven)."""

    @pytest.mark.parametrize("banned", sorted(_BANNED_STANDALONE_ANCHORS))
    def test_banned_standalone_yields_no_spec_and_never_admits(
        self, banned: str
    ) -> None:
        """WHY: 'foundation'/'federal'/'india'/'trust' (and their institutional kin)
        carry zero topical signal alone — each was, or is the class of, a live false
        positive. A query that is ONLY the banned word derives NO admitting anchor,
        records it as a hygiene drop, and rejects a story that literally contains it."""
        derivation = derive_anchor_specs(banned)
        assert derivation.specs == []
        assert derivation.banned_anchors == [banned]
        assert not _admits(banned, f"A {banned} in the headline", f"{banned} entity")

    def test_banned_word_kept_inside_a_multi_word_phrase(self) -> None:
        """WHY: the ban is on the word STANDING ALONE, not on the word ever appearing.
        'foundation models' and 'russian federation' are specific phrases and must be
        kept — otherwise banning would gut legitimate interests."""
        assert [
            s.anchor_phrase for s in derive_anchor_specs("foundation models").specs
        ] == ["foundation models"]
        assert _admits("foundation models", "New foundation models beat the benchmark")
        assert _admits("russian federation", "Russian Federation signs the treaty")


class TestCriterion3ShortAnchorRequiresTitle:
    """A short/ambiguous anchor matching only the haystack is rejected; title passes."""

    def test_short_anchor_is_flagged_requires_title(self) -> None:
        """WHY: the flag is what the SQL twin reads — a 3-char single word is ambiguous
        ('fed', 'oil') and must be marked title-only."""
        (spec,) = derive_anchor_specs("fed").specs
        assert spec.requires_title is True

    def test_short_anchor_haystack_only_is_rejected(self) -> None:
        """WHY: 'fed' appearing only in an entity tag (Fed Cup tennis) is incidental,
        not what a monetary-policy interest means — reject the entity-only hit."""
        assert not _admits("fed", "WTA tour final in Madrid", entities="Fed Cup team")

    def test_short_anchor_in_title_admits(self) -> None:
        """WHY: 'fed' in the HEADLINE means the story is about it — admit."""
        assert _admits("fed", "Fed holds interest rates steady", entities="Powell")


class TestCriterion4ShortInterestThatIsTheSubject:
    """A legitimately short interest that IS the story's subject still passes."""

    def test_short_interest_in_title_still_admits(self) -> None:
        """WHY: the gate must not degrade into 'reject everything short'. A real IPL
        interest, when the story headline IS about the IPL, must admit."""
        assert _admits("ipl", "IPL final drama as Mumbai clinch the title")

    def test_short_interest_off_topic_still_rejected(self) -> None:
        """WHY: the same short anchor must still reject an unrelated story — proving the
        title requirement discriminates rather than blanket-passing short interests."""
        assert not _admits("ipl", "Local council debates new parking rules")


class TestCriterion5TermHygieneGapSurfaced:
    """An interest whose every anchor is banned/short is a visible term-hygiene gap.

    (The pure detection is here; the caller's structured WARNING log is asserted in
    ``test_gdelt_bigquery_adapter.py::TestLexicalKeyWiring``.)
    """

    def test_all_banned_is_a_hygiene_gap_with_no_specs(self) -> None:
        """WHY: an interest built only from banned generics can match NOTHING — it must
        be flagged, never silently unmatchable (Rule 12)."""
        derivation = derive_anchor_specs("foundation, trust")
        assert derivation.specs == []
        assert derivation.banned_anchors == ["foundation", "trust"]
        assert derivation.has_hygiene_gap is True

    def test_all_short_is_a_hygiene_gap_but_keeps_specs(self) -> None:
        """WHY: an interest whose only anchors are short/ambiguous can match on the
        title alone — usable but fragile, so it is surfaced AND still enqueued."""
        derivation = derive_anchor_specs("fed, ipl")
        assert [s.anchor_phrase for s in derivation.specs] == ["fed", "ipl"]
        assert all(s.requires_title for s in derivation.specs)
        assert derivation.has_hygiene_gap is True

    def test_strong_anchor_present_is_not_a_gap(self) -> None:
        """WHY: one specific admit-alone anchor is enough — no false alarm."""
        assert (
            derive_anchor_specs("artificial intelligence, fed").has_hygiene_gap is False
        )

    def test_empty_query_is_not_a_hygiene_gap(self) -> None:
        """WHY: an all-stopword/empty query is the separate 'no usable terms' skip, not
        a term-hygiene gap — the two must not be conflated (different fix_suggestions)."""
        derivation = derive_anchor_specs("the and news latest")
        assert derivation.specs == []
        assert derivation.banned_anchors == []
        assert derivation.has_hygiene_gap is False


class TestUncommaedSoupQueryGuard:
    """A comma-less multi-word query is a soup query — one unmatchable mega-anchor.

    WHY (issue #69): the 2026-07-04 catalog seed wrote space-joined keyword soup
    ('humanoid robots robotics news') where the contract wants comma-joined anchor
    phrases. Such a query derives exactly ONE anchor demanding the whole sentence
    CONTIGUOUSLY, so the interest matches nothing and ingests nothing — silently,
    because it has a spec and a *strong* one, so ``has_hygiene_gap`` stays False.
    On 2026-07-25 that shape returned ``rows_returned: 0`` across all 26 of the
    founder's followed interests. This predicate is what makes it loud (Rule 12).
    """

    def test_soup_query_is_flagged(self) -> None:
        """WHY: the exact live shape — no commas, four+ words — must be detected."""
        derivation = derive_anchor_specs("humanoid robots robotics news")
        assert derivation.has_uncommaed_soup_query is True
        # ...and it is invisible to the pre-existing gap check, which is the trap:
        assert derivation.has_hygiene_gap is False
        assert [s.anchor_phrase for s in derivation.specs] == ["humanoid robots robotics"]

    def test_commaed_query_is_never_soup(self) -> None:
        """WHY: a comma means the author used the anchor-list contract — never warn."""
        soup_length_but_commaed = "data center buildout, hyperscale data center, Stargate"
        assert derive_anchor_specs(soup_length_but_commaed).has_uncommaed_soup_query is False

    def test_short_comma_less_phrase_is_not_soup(self) -> None:
        """WHY (boundary): a genuine 3-token phrase ('Supreme Court ruling') is a
        legitimate single anchor — flagging it would train the reader to ignore the
        warning. The threshold is >= 4 tokens, and 3 must stay silent."""
        assert derive_anchor_specs("Supreme Court ruling").has_uncommaed_soup_query is False
        assert derive_anchor_specs("large language models news").has_uncommaed_soup_query is True

    def test_filler_words_still_count_toward_the_soup_threshold(self) -> None:
        """WHY: fillers are dropped from the anchor but NOT from the diagnosis —
        'hurricane tropical storm news' derives the unmatchable 3-token phrase
        'hurricane tropical storm', and it is soup precisely because the author was
        listing keywords. Counting raw words is what catches it."""
        assert derive_anchor_specs("hurricane tropical storm news").has_uncommaed_soup_query is True

    def test_empty_query_is_not_soup(self) -> None:
        """WHY: an empty/queryless interest is the separate 'no usable terms' skip."""
        assert derive_anchor_specs("").has_uncommaed_soup_query is False


class TestAcronymOnlyInterestsSurvive:
    """Acronym-only interests derive a title-gated anchor — they are never dropped.

    WHY (issue #69 edge case): 'GDP' / 'CPI' are the whole interest for the founder's
    macro slices. A backfill that dropped them (or a tokenizer floor that ate them)
    would silently delete those interests; the contract is that they survive as
    ``requires_title`` anchors — matchable, just only when the story is ABOUT them.
    """

    @pytest.mark.parametrize("acronym", ["GDP", "CPI", "NBA", "NFL"])
    def test_acronym_derives_one_title_gated_spec(self, acronym: str) -> None:
        derivation = derive_anchor_specs(acronym)
        assert [s.anchor_phrase for s in derivation.specs] == [acronym.lower()]
        assert derivation.specs[0].requires_title is True
        assert derivation.banned_anchors == []

    def test_acronym_paired_with_a_strong_anchor_clears_the_gap(self) -> None:
        """WHY: the backfill's fix for an acronym-only interest is to ADD a strong
        companion anchor, not to remove the acronym — both must survive."""
        derivation = derive_anchor_specs("GDP, gross domestic product")
        assert [s.requires_title for s in derivation.specs] == [True, False]
        assert derivation.has_hygiene_gap is False


class TestRc3KnownFalsePositives:
    """The four named RC3 false positives — three the lexical key closes, one #51's."""

    def test_venison_does_not_admit_to_ai_via_foundation(self) -> None:
        """07-07: a venison donation matched AI interests via 'Foundation'. With
        'foundation' banned standalone and 'foundation models' absent as a phrase, the
        AI interest rejects it."""
        assert not _admits(
            "foundation models, artificial intelligence",
            "Rotary club donates venison to the county food shelter",
            entities="Smith Family Foundation; Rotary International",
        )

    def test_hawaii_funding_does_not_admit_to_fed_via_federal(self) -> None:
        """07-07: 'Federal funding in Hawaii' matched interest-rates-fed via 'Federal'.
        'federal' is banned standalone; 'federal reserve' phrase is absent; and \\bfed\\b
        does not match inside 'federal' — so it is rejected."""
        assert not _admits(
            "federal reserve, interest rates, fed",
            "Federal funding boosts Hawaii public school budgets",
            entities="State of Hawaii; Department of Education",
        )

    def test_indonesia_port_does_not_admit_to_cricket_via_india(self) -> None:
        """07-07: an Indonesia-port story matched cricket.india via 'India'. 'india' is
        banned standalone; 'india cricket' phrase is absent; 'bcci'/'ipl' are short and
        absent from the title — so cricket.india rejects it."""
        assert not _admits(
            "india cricket, bcci, ipl",
            "Indonesia expands Surabaya port capacity for regional trade",
            entities="Indian Ocean shipping lanes; Port of Surabaya",
        )

    def test_zoning_data_center_admits_lexically_and_is_deferred_to_semantic_key(
        self,
    ) -> None:
        """07-07: a local zoning lawsuit matched data-center-buildout via 'data center'.
        Unlike the other three, this story GENUINELY contains the anchor phrase 'data
        center' (it is about a data-center facility), so the LEXICAL key correctly
        admits it on phrase grounds — it is not a lexical false positive but a
        notability/relevance one (a local court notice, not buildout news). Closing it
        needs the SEMANTIC key + notability gate (slice #51 / WS-B). This test pins that
        boundary so #51 has its regression anchor and we never claim the lexical key
        alone closes all four RC3 cases."""
        assert _admits(
            "data center, hyperscale campus",
            "Residents sue over rezoning for a new data center",
            entities="County Planning Board",
        )
