"""Unit tests for the GDELT BigQuery adapter (bulk-ingest path).

No BigQuery, no network: the BigQuery client is faked at the boundary
(``make_fake_bq_client`` captures the SQL + query parameters so we assert the
wiring without a live query). Covers term classification (the precision rule),
row→candidate mapping, GKG date parsing, the batched search (stamping, the
struct-array params, skip-empty, no-call-on-empty), failure normalization, and
the recall ``search()`` path.

WHY these matter: the matcher's precision (anchor-term selection) and the
per-interest cap wiring are exactly what fixed the v1 noise + clustering blow-up;
a regression here silently reintroduces either. The clustering-merge test encodes
that one article matched by two interests still collapses to ONE canonical story.

    >>> pytest tests/agents/ingestion/test_gdelt_bigquery_adapter.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from structlog.testing import capture_logs

from agents.ingestion.adapters.gdelt_bigquery import (
    _BATCH_SQL,
    GdeltBigQueryAdapter,
    build_domain_filter_sql,
    match_terms,
    recall_terms,
)
from agents.ingestion.candidate_language import build_language_filter_sql
from agents.ingestion.dedup import StoryClusterer
from agents.ingestion.models import ActiveInterest
from agents.shared.exceptions import AdapterFetchError

_SINCE = datetime(2026, 5, 30, 12, 30, 0, tzinfo=timezone.utc)


def _param(job_config, name):
    """Fetch a query parameter by name from a captured QueryJobConfig."""
    return next(p for p in job_config.query_parameters if p.name == name)


class TestTermClassification:
    """match_terms / recall_terms — the precision rule that de-noised v1."""

    def test_entity_list_query_keeps_all_entities(self) -> None:
        """An OR-list of entities keeps each (any one alone is on-topic)."""
        assert match_terms("semiconductor stocks NVIDIA TSMC news") == [
            "semiconductor",
            "nvidia",
            "tsmc",
        ]

    def test_country_plus_topic_keeps_both_for_cooccurrence_ranking(self) -> None:
        """India + cricket + BCCI all survive; ranking (not exclusion) prioritizes
        co-occurrence, so an India-cricket story outranks an India-politics one."""
        assert match_terms("India cricket team BCCI news") == [
            "india",
            "cricket",
            "bcci",
        ]

    def test_generic_event_word_dropped_from_anchors(self) -> None:
        """'war' is a content-free event word — dropped from precision anchors so it
        cannot match alone (it matched a D-Day history piece in v1)."""
        assert match_terms("Ukraine Russia war news") == ["ukraine", "russia"]
        assert "war" not in match_terms("Ukraine Russia war news")

    def test_recall_keeps_event_words(self) -> None:
        """The recall path (census) keeps event words for a wider match."""
        assert recall_terms("Ukraine Russia war news") == ["ukraine", "russia", "war"]

    def test_match_and_recall_differ_on_event_words(self) -> None:
        """The two builders diverge exactly on the generic-broad set."""
        assert match_terms("Ukraine Russia war news") != recall_terms(
            "Ukraine Russia war news"
        )

    def test_all_stopwords_yields_empty(self) -> None:
        """A query of pure stopwords yields no anchor terms (interest is skipped)."""
        assert match_terms("the and for news latest") == []

    def test_only_generic_broad_falls_back_to_keeping_them(self) -> None:
        """An interest written ONLY from event words still matches something
        (fallback re-includes them) rather than silently ingesting nothing."""
        assert match_terms("war crisis news") == ["war", "crisis"]

    def test_short_tokens_and_dedup(self) -> None:
        """<3-char tokens dropped; duplicates collapsed, order preserved."""
        assert match_terms("AI AI OpenAI OpenAI models") == ["openai", "models"]


class TestRowsToCandidates:
    """_rows_to_candidates — GKG row dicts → typed CandidateStory."""

    def test_stamped_candidate_carries_matched_interest(self, make_bq_row) -> None:
        """A well-formed row stamps the matched interest id + slug."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://cnn.com/x",
                "Title X",
                "cnn.com",
                interest_id="int-1",
                interest_slug="slug-1",
                sharing_image="https://cnn.com/x.jpg",
            )
        ]
        cands = adapter._rows_to_candidates(rows, stamp_interest=True)
        assert len(cands) == 1
        c = cands[0]
        assert c.candidate_matched_interest_id == "int-1"
        assert c.candidate_matched_interest_slug == "slug-1"
        assert c.candidate_url == "https://cnn.com/x"
        assert c.candidate_outlet_domain == "cnn.com"
        assert c.candidate_social_image_url == "https://cnn.com/x.jpg"
        assert c.candidate_body_text is None

    def test_rows_missing_required_fields_are_skipped(self, make_bq_row) -> None:
        """Rows with no url / title / outlet are dropped (cannot be a candidate)."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row("", "Has no url", "cnn.com"),
            make_bq_row("https://x.com/a", "", "x.com"),
            make_bq_row("https://x.com/b", "Has no outlet", ""),
            make_bq_row("https://ok.com/c", "Good", "ok.com"),
        ]
        cands = adapter._rows_to_candidates(rows, stamp_interest=True)
        assert len(cands) == 1
        assert cands[0].candidate_url == "https://ok.com/c"

    def test_unstamped_path_ignores_interest_id(self, make_bq_row) -> None:
        """stamp_interest=False (the search() path) never stamps an interest."""
        adapter = GdeltBigQueryAdapter()
        rows = [make_bq_row("https://x.com/a", "T", "x.com", interest_id="int-1")]
        cands = adapter._rows_to_candidates(rows, stamp_interest=False)
        assert cands[0].candidate_matched_interest_id is None

    def test_query_sentinel_id_is_not_stamped(self, make_bq_row) -> None:
        """The '__query__' sentinel id (recall path) is never stamped as an interest."""
        adapter = GdeltBigQueryAdapter()
        rows = [make_bq_row("https://x.com/a", "T", "x.com", interest_id="__query__")]
        cands = adapter._rows_to_candidates(rows, stamp_interest=True)
        assert cands[0].candidate_matched_interest_id is None

    def test_empty_social_image_is_none(self, make_bq_row) -> None:
        """A missing SharingImage maps to None, not an empty string."""
        adapter = GdeltBigQueryAdapter()
        rows = [make_bq_row("https://x.com/a", "T", "x.com", sharing_image=None)]
        assert (
            adapter._rows_to_candidates(rows, stamp_interest=True)[
                0
            ].candidate_social_image_url
            is None
        )

    def test_v2_themes_parsed_offset_stripped_and_deduped(self, make_bq_row) -> None:
        """V2Themes (CODE,offset;…) → offset-stripped, deduped, order-preserved
        codes in VERBATIM case (the theme→category whitelist keys on the uppercase
        GDELT codes — lowercasing here would silently break SP1's lookup)."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://x.com/a",
                "T",
                "x.com",
                v2_themes="WB_2670_JOBS,123;ECON_STOCKMARKET,456;WB_2670_JOBS,789",
            )
        ]
        cand = adapter._rows_to_candidates(rows, stamp_interest=True)[0]
        assert cand.candidate_themes == ["WB_2670_JOBS", "ECON_STOCKMARKET"]

    def test_v2_themes_none_yields_empty_list(self, make_bq_row) -> None:
        """A NULL V2Themes yields [] (the no-theme story must not crash ingest)."""
        adapter = GdeltBigQueryAdapter()
        rows = [make_bq_row("https://x.com/a", "T", "x.com", v2_themes=None)]
        assert (
            adapter._rows_to_candidates(rows, stamp_interest=True)[0].candidate_themes
            == []
        )

    def test_v2_themes_empty_string_yields_empty_list(self, make_bq_row) -> None:
        """An empty-string V2Themes yields [] (same fail-safe as NULL)."""
        adapter = GdeltBigQueryAdapter()
        rows = [make_bq_row("https://x.com/a", "T", "x.com", v2_themes="")]
        assert (
            adapter._rows_to_candidates(rows, stamp_interest=True)[0].candidate_themes
            == []
        )


class TestBatchSqlIncludesThemes:
    """Static guards: V2Themes must flow from the raw SELECT to the projection.

    WHY: themes are the category signal (M2). If V2Themes is dropped from `raw`
    or the final projection, every candidate gets candidate_themes=[] and category
    silently reverts to keyword-inherited — these substring asserts fail loud."""

    def test_raw_select_includes_v2_themes(self) -> None:
        """V2Themes is selected in the `raw` CTE (alongside the entity columns)."""
        assert "V2Persons, V2Organizations, V2Locations, V2Themes" in _BATCH_SQL

    def test_final_projection_includes_theme_column(self) -> None:
        """The v2_themes column reaches the final projection (so the parser sees it).

        Isolates the final ``SELECT … FROM ranked`` block (the LAST select, not the
        CTEs) so the assert fails if v2_themes is dropped only from the projection."""
        assert "FROM ranked" in _BATCH_SQL
        projection = _BATCH_SQL.rsplit("SELECT", 1)[1].split("FROM ranked", 1)[0]
        assert "v2_themes" in projection


class TestDomainFilterPredicate:
    """SP3: the additive trusted-outlet domain predicate (string-level, no BigQuery).

    WHY this matters: the predicate is what makes the GKG path trusted-outlet-only —
    it is the alternate to the DOC ``domainis:`` fetch. If it is missing when domains
    are given the GKG path fetches every outlet (not trusted-only); if it is present
    when no domains are given the no-domains SQL diverges from today's (the path is
    no longer additive/reversible). Both are encoded below.
    """

    def test_builder_emits_in_unnest_predicate(self) -> None:
        """The pure builder emits the LOWER(SourceCommonName) IN UNNEST predicate."""
        assert build_domain_filter_sql() == (
            "AND LOWER(SourceCommonName) IN UNNEST(@domains)"
        )

    @pytest.mark.asyncio
    async def test_domains_inject_predicate_and_bind_lowercased_array(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """With domains supplied: the SQL contains the IN-UNNEST predicate and the
        job binds an ArrayQueryParameter('domains', 'STRING', [...]) lowercased."""
        from agents.ingestion.models import ActiveInterest

        client = make_fake_bq_client(
            rows=[make_bq_row("https://reuters.com/x", "T", "reuters.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="a", interest_slug="a", interest_search_query="Arsenal FC"
            )
        ]

        await adapter.search_active_interests(
            active, _SINCE, domains=["Reuters.com", "APNews.com"]
        )

        sql = client.captured["sql"]
        assert "LOWER(SourceCommonName) IN UNNEST(@domains)" in sql
        # the predicate sits in the raw WHERE, not in the projection (V2Themes intact)
        assert "V2Persons, V2Organizations, V2Locations, V2Themes" in sql
        domains_param = _param(client.captured["job_config"], "domains")
        assert domains_param.array_type == "STRING"
        assert domains_param.values == ["reuters.com", "apnews.com"]  # lowercased

    @pytest.mark.asyncio
    async def test_no_domains_sql_equals_batch_sql_and_binds_no_domains_param(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """Without domains: the emitted SQL equals the current _BATCH_SQL (additive /
        reversible) and no @domains param is bound."""
        from agents.ingestion.models import ActiveInterest

        client = make_fake_bq_client(
            rows=[make_bq_row("https://reuters.com/x", "T", "reuters.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="a", interest_slug="a", interest_search_query="Arsenal FC"
            )
        ]

        await adapter.search_active_interests(active, _SINCE)

        assert client.captured["sql"] == _BATCH_SQL  # byte-identical to today
        names = {p.name for p in client.captured["job_config"].query_parameters}
        assert "domains" not in names


class TestParseGkgDate:
    """_parse_gkg_date — GKG INT64 YYYYMMDDHHMMSS → UTC datetime."""

    def test_valid_int_date(self) -> None:
        assert GdeltBigQueryAdapter._parse_gkg_date(20260531101500) == datetime(
            2026, 5, 31, 10, 15, 0, tzinfo=timezone.utc
        )

    def test_valid_str_date(self) -> None:
        assert GdeltBigQueryAdapter._parse_gkg_date("20260531101500") == datetime(
            2026, 5, 31, 10, 15, 0, tzinfo=timezone.utc
        )

    def test_none_falls_back_to_now_utc(self) -> None:
        parsed = GdeltBigQueryAdapter._parse_gkg_date(None)
        assert parsed.tzinfo == timezone.utc

    def test_malformed_falls_back_to_now_utc(self) -> None:
        parsed = GdeltBigQueryAdapter._parse_gkg_date("not-a-date")
        assert parsed.tzinfo == timezone.utc


class TestSearchActiveInterests:
    """search_active_interests — the batched path: stamping, params, skips."""

    @pytest.mark.asyncio
    async def test_stamps_and_caps_via_params(
        self, make_fake_bq_client, make_bq_row, interest_nodes, interest_ids
    ) -> None:
        """One usable interest → one struct in @interest_terms; rows stamp the
        interest; the per-interest cap + floored partition are passed as params."""
        from agents.ingestion.models import ActiveInterest

        client = make_fake_bq_client(
            rows=[
                make_bq_row(
                    "https://cnn.com/x",
                    "Arsenal win",
                    "cnn.com",
                    interest_id=interest_ids["arsenal"],
                    interest_slug="sport.soccer.arsenal",
                )
            ]
        )
        adapter = GdeltBigQueryAdapter(client=client, per_interest_limit=75)
        active = [
            ActiveInterest(
                interest_id=interest_ids["arsenal"],
                interest_slug="sport.soccer.arsenal",
                # Reason: comma-joined anchor PHRASES (the seeder/interview convention) —
                # 'FC'/'news' are stopwords, so this yields 3 phrase anchors.
                interest_search_query="Arsenal, Premier League, Emirates Stadium",
            )
        ]

        cands = await adapter.search_active_interests(active, _SINCE)

        assert len(cands) == 1
        assert cands[0].candidate_matched_interest_id == interest_ids["arsenal"]
        job_config = client.captured["job_config"]
        # 3 phrase anchors (arsenal, premier league, emirates stadium) → 3 structs
        structs = _param(job_config, "interest_terms").values
        assert len(structs) == 3
        # each struct carries the lexical-key fields the SQL twin evaluates
        terms_by_regex = {s.struct_values["term"]: s.struct_values for s in structs}
        assert r"\barsenal\b" in terms_by_regex
        assert r"\bpremier\s+league\b" in terms_by_regex  # phrase, not two words
        # none of these are short/ambiguous, so none require a title match
        assert all(sv["requires_title"] is False for sv in terms_by_regex.values())
        assert _param(job_config, "per_interest_limit").value == 75
        # since_partition floored to midnight UTC of since's day
        assert _param(job_config, "since_partition").value == datetime(
            2026, 5, 30, 0, 0, 0, tzinfo=timezone.utc
        )
        assert _param(job_config, "since_date").value == 20260530123000

    @pytest.mark.asyncio
    async def test_interest_with_no_terms_is_skipped(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """An interest whose query is all stopwords is NOT enqueued as a predicate."""
        from agents.ingestion.models import ActiveInterest

        client = make_fake_bq_client(rows=[])
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="a", interest_slug="a", interest_search_query="Arsenal FC"
            ),
            ActiveInterest(
                interest_id="b", interest_slug="b", interest_search_query="the and news"
            ),
        ]

        await adapter.search_active_interests(active, _SINCE)

        # only interest 'a' (arsenal) contributes a term; 'b' is skipped
        terms = _param(client.captured["job_config"], "interest_terms").values
        assert len(terms) == 1

    @pytest.mark.asyncio
    async def test_empty_active_set_returns_empty_without_query(
        self, make_fake_bq_client
    ) -> None:
        """No usable interests → [] and BigQuery is never called (no wasted query)."""
        client = make_fake_bq_client(rows=[])
        adapter = GdeltBigQueryAdapter(client=client)

        assert await adapter.search_active_interests([], _SINCE) == []
        assert client.query.call_count == 0

    @pytest.mark.asyncio
    async def test_two_interests_one_article_merges_to_one_canonical(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """An article matched by two interests yields two stamped candidates that the
        clusterer collapses into ONE canonical story carrying BOTH interest ids.

        WHY: this is the dedup contract the batched path must preserve — the same
        URL surfaced for two interests is one real-world story, tagged to both."""
        from agents.ingestion.models import ActiveInterest

        url = "https://reuters.com/chips"
        client = make_fake_bq_client(
            rows=[
                make_bq_row(
                    url,
                    "TSMC ramps chip output",
                    "reuters.com",
                    interest_id="semis",
                    interest_slug="semis",
                ),
                make_bq_row(
                    url,
                    "TSMC ramps chip output",
                    "reuters.com",
                    interest_id="ai",
                    interest_slug="ai",
                ),
            ]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="semis",
                interest_slug="semis",
                interest_search_query="semiconductor TSMC",
            ),
            ActiveInterest(
                interest_id="ai",
                interest_slug="ai",
                interest_search_query="AI chips TSMC",
            ),
        ]

        cands = await adapter.search_active_interests(active, _SINCE)
        assert len(cands) == 2  # one per (article, interest)

        canonical = StoryClusterer().cluster_candidates(cands)
        assert len(canonical) == 1  # same URL → one story
        assert set(canonical[0].canonical_matched_interest_ids) == {"semis", "ai"}


class TestSearchActiveInterestsFailure:
    """A BigQuery error is normalized to AdapterFetchError (fail loud, named)."""

    @pytest.mark.asyncio
    async def test_query_error_raises_adapter_fetch_error(
        self, make_fake_bq_client
    ) -> None:
        from agents.ingestion.models import ActiveInterest

        client = make_fake_bq_client(raise_exc=RuntimeError("bq exploded"))
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="a", interest_slug="a", interest_search_query="Arsenal FC"
            )
        ]

        with pytest.raises(AdapterFetchError) as exc_info:
            await adapter.search_active_interests(active, _SINCE)
        assert exc_info.value.adapter_name == "gdelt_bigquery"


class TestSearchRecallPath:
    """search() — the single-query recall path used by the coverage census."""

    @pytest.mark.asyncio
    async def test_search_is_recall_and_unstamped(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """search() keeps event words (recall), uses the recall cap, and returns
        candidates with no interest stamped (the caller stamps)."""
        client = make_fake_bq_client(
            rows=[
                make_bq_row(
                    "https://x.com/a",
                    "Ukraine war latest",
                    "x.com",
                    interest_id="__query__",
                    interest_slug="__query__",
                )
            ]
        )
        adapter = GdeltBigQueryAdapter(client=client, recall_per_interest_limit=250)

        cands = await adapter.search("Ukraine Russia war news", _SINCE)

        assert len(cands) == 1
        assert cands[0].candidate_matched_interest_id is None
        job_config = client.captured["job_config"]
        # recall keeps 'war' → 3 terms (ukraine, russia, war), and uses the recall cap
        assert len(_param(job_config, "interest_terms").values) == 3
        assert _param(job_config, "per_interest_limit").value == 250

    @pytest.mark.asyncio
    async def test_search_empty_query_returns_empty_without_query(
        self, make_fake_bq_client
    ) -> None:
        client = make_fake_bq_client(rows=[])
        adapter = GdeltBigQueryAdapter(client=client)
        assert await adapter.search("the and for", _SINCE) == []
        assert client.query.call_count == 0


class TestLexicalKeyWiring:
    """The lexical key (slice #50) wired into the batched path — the SQL⇄Python twin.

    These prove the SQL admission gate is the twin of ``interest_lexical`` (behaviour
    tested in ``test_interest_lexical.py``): the JOIN evaluates the same predicate,
    the structs carry the phrase regex + ``requires_title`` flag, banned standalone
    generics never reach the query, and the term-hygiene gap is surfaced (criterion 5).
    """

    def test_sql_join_predicate_mirrors_evaluator(self) -> None:
        """WHY: the SQL cannot run offline, so its equivalence to
        ``evaluate_anchor_match`` rests on this predicate string. The JOIN must gate on
        the haystack AND require the title for a ``requires_title`` anchor — drop either
        clause and the lexical key silently regresses to bag-of-words admission."""
        assert "REGEXP_CONTAINS(b.hay, t.term)" in _BATCH_SQL
        assert (
            "AND (NOT t.requires_title OR REGEXP_CONTAINS(b.title_hay, t.term))"
            in _BATCH_SQL
        )

    @pytest.mark.asyncio
    async def test_phrase_anchor_builds_contiguous_regex_struct(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """WHY: 'data center' must reach BigQuery as ONE contiguous-phrase regex, not
        two independent word terms — the difference between rejecting and admitting the
        zoning-lawsuit false positive class."""
        client = make_fake_bq_client(
            rows=[make_bq_row("https://x.com/a", "T", "x.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="dc",
                interest_slug="tech.data-center-buildout",
                interest_search_query="data center buildout",
            )
        ]

        await adapter.search_active_interests(active, _SINCE)

        structs = _param(client.captured["job_config"], "interest_terms").values
        assert len(structs) == 1
        assert structs[0].struct_values["term"] == r"\bdata\s+center\s+buildout\b"
        assert structs[0].struct_values["requires_title"] is False

    @pytest.mark.asyncio
    async def test_short_anchor_struct_requires_title(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """WHY: the ``requires_title`` flag is what the SQL twin reads to gate a
        short/ambiguous anchor to a title match — it must be True on the struct."""
        client = make_fake_bq_client(
            rows=[make_bq_row("https://x.com/a", "T", "x.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="cric",
                interest_slug="sport.cricket.ipl",
                interest_search_query="india cricket, ipl",
            )
        ]

        await adapter.search_active_interests(active, _SINCE)

        structs = _param(client.captured["job_config"], "interest_terms").values
        flags = {
            s.struct_values["term"]: s.struct_values["requires_title"] for s in structs
        }
        assert flags[r"\bindia\s+cricket\b"] is False  # phrase → haystack ok
        assert flags[r"\bipl\b"] is True  # short → title required

    @pytest.mark.asyncio
    async def test_banned_only_interest_skipped_logs_hygiene_gap_no_query(
        self, make_fake_bq_client
    ) -> None:
        """WHY (criterion 5): an interest built only from banned generics can match
        NOTHING. It must be skipped with a structured ``interest_term_hygiene_gap``
        warning naming the banned anchors + a fix_suggestion — never silently
        unmatchable — and must not waste a BigQuery query."""
        client = make_fake_bq_client(rows=[])
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="bad",
                interest_slug="ai.foundation",
                interest_search_query="foundation, trust",
            )
        ]

        with capture_logs() as logs:
            result = await adapter.search_active_interests(active, _SINCE)

        assert result == []
        assert client.query.call_count == 0  # no term predicates → no wasted query
        gaps = [log for log in logs if log["event"] == "interest_term_hygiene_gap"]
        assert len(gaps) == 1
        assert gaps[0]["log_level"] == "warning"
        assert gaps[0]["interest_slug"] == "ai.foundation"
        assert gaps[0]["banned_anchors"] == ["foundation", "trust"]
        assert gaps[0]["usable_anchors"] == 0
        assert "fix_suggestion" in gaps[0]

    @pytest.mark.asyncio
    async def test_all_short_interest_enqueues_but_logs_hygiene_gap(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """WHY (criterion 5): an interest whose only anchors are short/ambiguous is
        usable (title-only) but fragile — it is still enqueued (a term IS sent) AND the
        hygiene gap is surfaced so the anchor set can be strengthened."""
        client = make_fake_bq_client(
            rows=[make_bq_row("https://x.com/a", "T", "x.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="short",
                interest_slug="markets.oil",
                interest_search_query="oil, gas",
            )
        ]

        with capture_logs() as logs:
            await adapter.search_active_interests(active, _SINCE)

        # the interest is NOT skipped — its short anchors are still queried
        assert client.query.call_count == 1
        structs = _param(client.captured["job_config"], "interest_terms").values
        assert len(structs) == 2
        assert all(s.struct_values["requires_title"] is True for s in structs)
        gaps = [log for log in logs if log["event"] == "interest_term_hygiene_gap"]
        assert len(gaps) == 1
        assert gaps[0]["interest_slug"] == "markets.oil"
        assert gaps[0]["strong_anchors"] == 0
        assert "fix_suggestion" in gaps[0]

    @pytest.mark.asyncio
    async def test_soup_query_enqueues_but_logs_hygiene_gap(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """WHY (issue #69): a comma-less keyword-soup query derives ONE mega-anchor that
        demands the whole sentence contiguously — the interest ingests NOTHING while
        looking perfectly healthy (it has a strong spec, so the banned/short gaps stay
        silent). That is how the 2026-07-25 run returned zero rows for all 26 followed
        interests with no warning at all. It must warn on the same channel, tagged with
        its own ``hygiene_gap_kind`` so a reader can tell the three modes apart."""
        client = make_fake_bq_client(
            rows=[make_bq_row("https://x.com/a", "T", "x.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="soup",
                interest_slug="ai.humanoid-robots",
                interest_search_query="humanoid robots robotics news",
            )
        ]

        with capture_logs() as logs:
            await adapter.search_active_interests(active, _SINCE)

        # still enqueued — the guard diagnoses, it does not drop the interest
        assert client.query.call_count == 1
        gaps = [log for log in logs if log["event"] == "interest_term_hygiene_gap"]
        assert len(gaps) == 1
        assert gaps[0]["log_level"] == "warning"
        assert gaps[0]["interest_slug"] == "ai.humanoid-robots"
        assert gaps[0]["hygiene_gap_kind"] == "uncommaed_soup_query"
        assert "comma" in gaps[0]["fix_suggestion"]

    @pytest.mark.asyncio
    async def test_hygiene_gap_kinds_are_distinguishable_on_the_shared_channel(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """WHY: three different failures share the ``interest_term_hygiene_gap`` event.
        Without an explicit discriminator a reader cannot tell "unmatchable soup" from
        "all anchors banned" from "title-only anchors" — the shared-channel trap that
        cost issue #70 a whole batch. Every emission carries ``hygiene_gap_kind``."""
        client = make_fake_bq_client(
            rows=[make_bq_row("https://x.com/a", "T", "x.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="banned",
                interest_slug="ai.foundation",
                interest_search_query="foundation, trust",
            ),
            ActiveInterest(
                interest_id="short",
                interest_slug="markets.oil",
                interest_search_query="oil, gas",
            ),
            ActiveInterest(
                interest_id="soup",
                interest_slug="ai.humanoid-robots",
                interest_search_query="humanoid robots robotics news",
            ),
        ]

        with capture_logs() as logs:
            await adapter.search_active_interests(active, _SINCE)

        kinds = {
            log["interest_slug"]: log["hygiene_gap_kind"]
            for log in logs
            if log["event"] == "interest_term_hygiene_gap"
        }
        assert kinds == {
            "ai.foundation": "all_anchors_banned",
            "markets.oil": "no_strong_anchor",
            "ai.humanoid-robots": "uncommaed_soup_query",
        }

    @pytest.mark.asyncio
    async def test_healthy_interest_logs_no_hygiene_gap(
        self, make_fake_bq_client, make_bq_row
    ) -> None:
        """WHY: no false alarms — a well-formed interest with a specific admit-alone
        anchor must NOT emit the hygiene warning (Rule 12 cuts both ways: loud on real
        gaps, silent when healthy)."""
        client = make_fake_bq_client(
            rows=[make_bq_row("https://x.com/a", "T", "x.com")]
        )
        adapter = GdeltBigQueryAdapter(client=client)
        active = [
            ActiveInterest(
                interest_id="ai",
                interest_slug="ai.foundation-models",
                interest_search_query="foundation models, artificial intelligence",
            )
        ]

        with capture_logs() as logs:
            await adapter.search_active_interests(active, _SINCE)

        assert not [log for log in logs if log["event"] == "interest_term_hygiene_gap"]
        structs = _param(client.captured["job_config"], "interest_terms").values
        # 'foundation' alone banned, but 'foundation models' phrase kept
        terms = {s.struct_values["term"] for s in structs}
        assert r"\bfoundation\s+models\b" in terms
        assert r"\bfoundation\b" not in terms


class TestEnglishOnlyAdmissionWiring:
    """The English-only gate (slice #63) wired into the adapter — the SQL⇄Python twin.

    Behaviour lives in ``test_candidate_language.py`` (the pure module IS the
    specification). These prove the WIRING: the authoritative srclc rule reaches the
    SQL's ``raw`` CTE (so foreign rows never eat a per-interest top-K slot), the
    returned rows carry the stamp back so the Python backstop can re-check it, and
    both rejection and fail-open admission are logged — a silent filter would be
    indistinguishable from a broken query.
    """

    def test_raw_cte_carries_the_language_predicate(self) -> None:
        """WHY: this predicate is the cheap half. Dropped from the SQL, every
        machine-translated foreign document re-enters the ranking window and the
        top-K cap starts spending slots on stories no reader of blip can use."""
        assert build_language_filter_sql() in _BATCH_SQL
        assert "TranslationInfo" in _BATCH_SQL

    def test_translation_info_reaches_the_final_projection(self) -> None:
        """WHY: the Python backstop re-reads the stamp off the row. If the column is
        dropped from the projection the gate degrades to title-only WITHOUT any error
        — exactly the silent downgrade Rule 12 forbids."""
        projection = _BATCH_SQL.rsplit("SELECT", 1)[1].split("FROM ranked", 1)[0]
        assert "translation_info" in projection

    def test_non_english_row_is_dropped_and_counted(self, make_bq_row) -> None:
        """A German row from the 2026-07-20 shortlist never becomes a candidate, and
        the drop is reported with its reason (aggregate, not per-row)."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://gamezone.de/a",
                "Valve warnt: PC-Hardware k&#xF6;nnte noch teurer werden",
                "gamezone.de",
            ),
            make_bq_row(
                "https://cnbc.com/b",
                "TSMC is accelerating Arizona fab buildout to capitalize on AI demand",
                "cnbc.com",
            ),
        ]

        with capture_logs() as logs:
            cands = adapter._rows_to_candidates(rows, stamp_interest=True)

        assert [c.candidate_outlet_domain for c in cands] == ["cnbc.com"]
        rejected = [
            log for log in logs if log["event"] == "gdelt_non_english_candidates_rejected"
        ]
        assert len(rejected) == 1
        assert rejected[0]["rejected_total"] == 1
        assert rejected[0]["rejected_by_reason"] == {"foreign_function_words": 1}

    def test_gdelt_stamp_on_the_row_rejects_a_fluent_english_title(
        self, make_bq_row
    ) -> None:
        """WHY: the albeu.com case — an Albanian story whose title arrived translated.
        Only the stamp can catch it, and the adapter must actually read the column."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://albeu.com/a",
                "The canvasser pokes his nose into the private lives of Albanians",
                "albeu.com",
                translation_info="srclc:sqi;eng:Moses 2.1.1",
            )
        ]
        assert adapter._rows_to_candidates(rows, stamp_interest=True) == []

    def test_english_stamped_row_carries_its_language_code(self, make_bq_row) -> None:
        """An admitted row records the source language GDELT reported (None when the
        stamp was blank) — so the decision is inspectable downstream, not inferred."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://cnbc.com/a",
                "TSMC accelerates its Arizona fab buildout",
                "cnbc.com",
                translation_info="srclc:eng;eng:passthru",
            ),
            make_bq_row("https://cnbc.com/b", "Fed holds rates steady", "cnbc.com"),
        ]
        cands = adapter._rows_to_candidates(rows, stamp_interest=True)
        assert [c.candidate_language for c in cands] == ["eng", None]

    def test_blind_admission_is_admitted_and_warned(self, make_bq_row) -> None:
        """The boundary criterion: a row with NO language metadata and no English
        function words is ADMITTED (fail-open — over-rejection silently starves the
        pool) and the blind admission is counted in a structured warning."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://ibtimes.co.uk/a",
                "PlayStation 6 Release Date Leak: Sony Patent Reveals Cooling Upgrade",
                "ibtimes.co.uk",
            )
        ]

        with capture_logs() as logs:
            cands = adapter._rows_to_candidates(rows, stamp_interest=True)

        assert len(cands) == 1
        blind = [log for log in logs if log["event"] == "gdelt_language_admission_blind"]
        assert len(blind) == 1
        assert blind[0]["blind_admissions"] == 1
        assert blind[0]["log_level"] == "warning"
        assert "fix_suggestion" in blind[0]

    def test_all_english_rows_log_nothing(self, make_bq_row) -> None:
        """WHY: the gate must be quiet on a clean batch — a warning that fires every
        run is a warning nobody reads (and the fail-open count would be meaningless)."""
        adapter = GdeltBigQueryAdapter()
        rows = [
            make_bq_row(
                "https://cnbc.com/a",
                "TSMC is accelerating its Arizona fab buildout to meet AI demand",
                "cnbc.com",
            )
        ]

        with capture_logs() as logs:
            adapter._rows_to_candidates(rows, stamp_interest=True)

        assert not [
            log
            for log in logs
            if log["event"]
            in {"gdelt_non_english_candidates_rejected", "gdelt_language_admission_blind"}
        ]
