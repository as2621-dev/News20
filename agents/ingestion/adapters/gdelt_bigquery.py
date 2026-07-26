"""GDELT BigQuery news adapter — the unthrottled bulk-ingest path.

The keyless GDELT DOC 2.0 API (``gdelt_doc.py``) is per-IP rate-limited (~1 req/5s,
sticky 429s) and caps **every** call at 250 records. This adapter reads the SAME
GDELT firehose from the **public BigQuery dataset** ``gdelt-bq.gdeltv2.gkg_partitioned``
instead — which has **no rate limit and no 250-record cap**, so one SQL query
filters *all* active interests at once over the recent window (validated 2026-06-06:
~273K articles/day, as fresh as the real-time table, ~0.67 GB scanned for a 2.5-day
window ≈ $0.004, free under the 1 TB/mo tier).

Two phases mirror ``BaseNewsAdapter``:
  • ``search_active_interests()`` (the batched win) — ONE query for the whole
    active-interest set; each returned row is attributed to the interest whose
    terms matched it and emitted as one ``CandidateStory`` per (article, interest)
    so the existing clusterer dedups exactly as for the DOC per-interest fan-out.
  • ``search()`` — single-query convenience (ABC contract; also the path the
    Phase-2c coverage census reuses). Interest-agnostic: the pipeline stamps.

**Matching (the lexical key + relevance ranking).** An interest's free-text
``interest_search_query`` is a comma-joined list of anchor *phrases*; each becomes
one contiguous-phrase anchor via ``interest_lexical.derive_anchor_specs`` (banned
standalone generics dropped, short/ambiguous anchors flagged ``requires_title``).
A GKG row matches an interest when its
``title + V2Persons + V2Organizations + V2Locations`` haystack contains ANY anchor
phrase (word-boundary regex) AND — for a ``requires_title`` anchor — the title
contains it too. This is the lexical half of the two-key relevance lock (RC3); the
semantic half (embedding similarity) is slice #51. To suppress broad-term noise
WITHOUT hard exclusions, each interest's matches are ranked by the
**number of distinct anchor terms matched**
(then recency) and capped to the top ``per_interest_limit`` (default 75, mirroring
the DOC ``maxrecords``). So for "India cricket … BCCI", an India+cricket article
(2 terms) outranks an India-politics article (1 term), and the cap bounds the pool
so the O(n²) clusterer stays fast. (Chosen over a hard ``country AND topic`` rule,
which over-excludes e.g. "Israel Gaza ceasefire" articles lacking the word
"ceasefire".) No interest→entity link exists in the schema, so matching is
query-driven; the entity registry stays a ranking-time concern.

**Language (English-only admission).** blip is an English-language product, so every
candidate must clear ``candidate_language.evaluate_language_admission`` — a
deterministic, zero-cost gate (no LLM, no language-ID API). Its authoritative half
(GDELT's ``TranslationInfo`` source-language stamp) is pushed into ``_BATCH_SQL``'s
``raw`` CTE so a translated foreign-language document never consumes a per-interest
top-K slot; its title-side backstop runs over the returned rows in
``_rows_to_candidates``. Both fail OPEN — a row with no language evidence is admitted
and counted in the ``gdelt_language_admission_blind`` warning, never dropped silently.

Body extraction is source-agnostic (fetch the URL + ``trafilatura``), so it is
delegated to a composed ``GdeltDocAdapter`` rather than duplicated.

GKG specifics (verified against live schema 2026-06-06):
  • Article TITLE lives in ``Extras`` as ``<PAGE_TITLE>…</PAGE_TITLE>`` (not a column).
  • ``DATE`` is an INT64 ``YYYYMMDDHHMMSS``; ``SourceCommonName`` is the outlet
    domain; ``SharingImage`` is the social image; ``DocumentIdentifier`` is the URL.
  • Partitioned at **midnight granularity** on ``_PARTITIONTIME`` — floor the
    filter to the start of the day or a ``now - 1 day`` filter lands mid-partition
    and undercounts.
"""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.adapters.gdelt_doc import GdeltDocAdapter
from agents.ingestion.candidate_language import (
    LANGUAGE_EVIDENCE_NONE,
    build_language_filter_sql,
    evaluate_language_admission,
)
from agents.ingestion.candidate_title import clean_candidate_title
from agents.ingestion.models import ActiveInterest, CandidateStory
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

_ADAPTER_NAME = "gdelt_bigquery"
_GKG_DATE_FORMAT = "%Y%m%d%H%M%S"

_DEFAULT_MAX_ROWS = 5000
_DEFAULT_PER_INTEREST_LIMIT = 75  # mirrors gdelt_doc._DEFAULT_MAX_RECORDS
_DEFAULT_RECALL_PER_INTEREST_LIMIT = 250  # search()/census recall cap

# Reason: filler/relevance words common in the DOC-era ``interest_search_query``
# strings ("Arsenal FC news", "semiconductor stocks NVIDIA TSMC news") that carry
# no entity signal; dropped before tokens become anchor terms.
_QUERY_STOPWORDS: frozenset[str] = frozenset(
    {
        "news",
        "latest",
        "update",
        "updates",
        "live",
        "today",
        "the",
        "and",
        "for",
        "with",
        "from",
        "team",
        "stock",
        "stocks",
        "market",
        "markets",
        "report",
        "reports",
        "vs",
        "var",
        "fc",
        "official",
    }
)

# Reason: content-free EVENT words that match across every conflict/topic and so
# create noise as anchors ("war" matched a D-Day history piece in v1). Dropped from
# the precision (``match_terms``) anchor set but KEPT in the recall set. If an
# interest is ONLY these words, match_terms falls back to including them.
_GENERIC_BROAD: frozenset[str] = frozenset(
    {
        "war",
        "crisis",
        "attack",
        "talks",
        "summit",
        "clash",
        "unrest",
        "tensions",
        "conflict",
        "warning",
        "threat",
        "fears",
        "breaking",
        "deal",
    }
)


def _tokenize(search_query: str) -> list[str]:
    """Lowercase alphanumeric tokens of length >= 3, in order."""
    return [t for t in re.findall(r"[a-z0-9]+", search_query.lower()) if len(t) >= 3]


def _dedup(tokens: list[str]) -> list[str]:
    """De-duplicate preserving first-seen order."""
    seen: set[str] = set()
    return [t for t in tokens if not (t in seen or seen.add(t))]


def match_terms(search_query: str) -> list[str]:
    """Anchor terms for PRECISION matching (search_active_interests).

    Drops stopwords + content-free event words. If that empties the set (an
    interest written entirely from those words), falls back to dropping only
    stopwords so the interest still matches something.

    Example:
        >>> match_terms("India cricket team BCCI news")
        ['india', 'cricket', 'bcci']
        >>> match_terms("Ukraine Russia war news")
        ['ukraine', 'russia']
    """
    tokens = _tokenize(search_query)
    anchors = [
        t for t in tokens if t not in _QUERY_STOPWORDS and t not in _GENERIC_BROAD
    ]
    if not anchors:
        anchors = [t for t in tokens if t not in _QUERY_STOPWORDS]
    return _dedup(anchors)


def recall_terms(search_query: str) -> list[str]:
    """Terms for RECALL matching (search()/coverage census) — stopwords only.

    Keeps event words (broader match) since the census wants the whole coverage
    landscape for one story.

    Example:
        >>> recall_terms("Ukraine Russia war news")
        ['ukraine', 'russia', 'war']
    """
    return _dedup([t for t in _tokenize(search_query) if t not in _QUERY_STOPWORDS])


def _term_regex(token: str) -> str:
    """A word-boundary RE2 pattern for one lowercased token."""
    return r"\b" + re.escape(token) + r"\b"


def _parse_v2_themes(v2_themes: Any) -> list[str]:
    """Parse a GKG ``V2Themes`` string into deduped theme codes (verbatim case).

    ``V2Themes`` is ``CODE,charoffset;CODE,charoffset;…`` — semicolon-delimited
    entries, each ``CODE,offset``. We split on ``;``, take the part before the
    first ``,`` (the code), strip whitespace, drop empties, and dedup preserving
    first-seen order. NULL/empty/missing → ``[]`` (never raises).

    Case is kept VERBATIM (GDELT codes are uppercase) so the downstream
    theme→category whitelist — keyed on the uppercase GDELT codes — matches.

    Example:
        >>> _parse_v2_themes("WB_2670_JOBS,123;ECON_STOCKMARKET,456;WB_2670_JOBS,789")
        ['WB_2670_JOBS', 'ECON_STOCKMARKET']
    """
    if not v2_themes:
        return []
    seen: set[str] = set()
    codes: list[str] = []
    for entry in str(v2_themes).split(";"):
        code = entry.split(",", 1)[0].strip()
        if code and code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


# Reason: the English-only predicate (slice #63) — GDELT's own ``TranslationInfo``
# source-language stamp, evaluated in the ``raw`` CTE so a machine-translated
# foreign-language document never even consumes a per-interest top-K slot. Built by
# the pure ``candidate_language`` module so the SQL and its Python twin
# (``evaluate_language_admission``, which additionally applies the title-side backstop
# over the returned rows) cannot disagree on the authoritative rule.
_LANGUAGE_FILTER_SQL = build_language_filter_sql()

# Reason: one parameterized query — interest terms arrive as a STRUCT array
# (@interest_terms, one row per (interest, anchor)), so the SQL is fixed and
# injection-safe regardless of interest count. Each anchor is a PHRASE regex
# (``\bdata\s+center\b`` — contiguous, not a bag of words) carrying a
# ``requires_title`` flag; both are built by ``interest_lexical.derive_anchor_specs``
# (the lexical key — RC3). A row matches an interest when ANY anchor hits its
# entity+title haystack AND, for a short/ambiguous anchor (``requires_title``), also
# hits the title — the JOIN predicate below is the SQL half of the twin mirrored by
# ``interest_lexical.evaluate_anchor_match``. Matches are GROUPed per
# (article, interest) to count distinct matched anchors. Ranking puts TITLE matches
# first (title_match_count) — an anchor in the headline means the story is *about*
# the interest, vs an incidental body/entity-tag mention (which made multi-country
# roundups outrank focused stories) — then total match_count, then recency.
_BATCH_SQL = rf"""
WITH raw AS (
  SELECT
    DocumentIdentifier AS url,
    LOWER(SourceCommonName) AS outlet,
    DATE AS gkg_date,
    NULLIF(SharingImage, '') AS sharing_image,
    REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS title,
    V2Persons, V2Organizations, V2Locations, V2Themes, TranslationInfo
  FROM `gdelt-bq.gdeltv2.gkg_partitioned`
  WHERE _PARTITIONTIME >= @since_partition
    AND DATE >= @since_date
    AND DocumentIdentifier IS NOT NULL AND DocumentIdentifier != ''
    AND SourceCommonName IS NOT NULL AND SourceCommonName != ''
    {_LANGUAGE_FILTER_SQL}
),
base AS (
  SELECT
    url, outlet, gkg_date, sharing_image, title, V2Themes AS v2_themes,
    TranslationInfo AS translation_info,
    LOWER(IFNULL(title, '')) AS title_hay,
    LOWER(CONCAT(
      IFNULL(title, ''), ' ', IFNULL(V2Persons, ''), ' ',
      IFNULL(V2Organizations, ''), ' ', IFNULL(V2Locations, ''))) AS hay
  FROM raw
  WHERE title IS NOT NULL AND title != ''
),
matched AS (
  SELECT
    b.url, b.outlet, b.gkg_date, b.sharing_image, b.title, b.v2_themes,
    b.translation_info,
    t.interest_id, t.interest_slug,
    COUNT(DISTINCT t.term) AS match_count,
    COUNT(DISTINCT IF(REGEXP_CONTAINS(b.title_hay, t.term), t.term, NULL)) AS title_match_count
  FROM base AS b
  JOIN UNNEST(@interest_terms) AS t
    ON REGEXP_CONTAINS(b.hay, t.term)
   AND (NOT t.requires_title OR REGEXP_CONTAINS(b.title_hay, t.term))
  GROUP BY b.url, b.outlet, b.gkg_date, b.sharing_image, b.title, b.v2_themes,
           b.translation_info, t.interest_id, t.interest_slug
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY interest_id
    ORDER BY title_match_count DESC, match_count DESC, gkg_date DESC
  ) AS rn
  FROM matched
)
SELECT url, outlet, gkg_date, sharing_image, title, v2_themes, translation_info,
       interest_id, interest_slug, match_count, title_match_count
FROM ranked
WHERE rn <= @per_interest_limit
ORDER BY interest_id, rn
LIMIT @max_rows
"""

# Reason: the anchor predicate in the ``raw`` CTE WHERE clause that the optional
# domain filter is injected AFTER. It is the last ``raw``-WHERE line, so appending
# the domain predicate here keeps the SELECT (incl. M2's V2Themes) untouched — the
# domain path is line-disjoint from the projection (no M2/M4 collision).
_RAW_WHERE_ANCHOR = "AND SourceCommonName IS NOT NULL AND SourceCommonName != ''"

# Reason: the additive trusted-outlet predicate — restricts the ``raw`` CTE to the
# curated authority outlets via a bound STRING array (injection-safe like
# @interest_terms). ``outlet`` is ``LOWER(SourceCommonName)``, so the array is bound
# lowercased and compared against ``LOWER(SourceCommonName)``.
_DOMAIN_FILTER_SQL = "AND LOWER(SourceCommonName) IN UNNEST(@domains)"


def build_domain_filter_sql(param_name: str = "domains") -> str:
    """Return the GKG domain-restriction predicate for the ``raw`` CTE (pure).

    Emits ``AND LOWER(SourceCommonName) IN UNNEST(@<param_name>)`` — the additive
    trusted-outlet filter SP3 injects when a curated domain set is supplied. Kept a
    pure string builder (mirrors SP2's DOC builder) so it is unit-testable offline.

    Args:
        param_name: The bound array parameter name (default ``"domains"``).

    Returns:
        The SQL predicate fragment.

    Example:
        >>> build_domain_filter_sql()
        'AND LOWER(SourceCommonName) IN UNNEST(@domains)'
    """
    return f"AND LOWER(SourceCommonName) IN UNNEST(@{param_name})"


def _batch_sql_with_domains(domains: list[str] | None) -> str:
    """Return ``_BATCH_SQL`` with the domain predicate injected when domains given.

    With ``domains`` falsy the SQL is **identical** to ``_BATCH_SQL`` (the path is
    additive/reversible). With domains, the predicate is appended right after the
    last ``raw``-WHERE line — leaving the SELECT (incl. V2Themes) untouched.
    """
    if not domains:
        return _BATCH_SQL
    return _BATCH_SQL.replace(
        _RAW_WHERE_ANCHOR,
        f"{_RAW_WHERE_ANCHOR}\n    {build_domain_filter_sql()}",
        1,
    )


class GdeltBigQueryAdapter(BaseNewsAdapter):
    """News adapter backed by the GDELT BigQuery GKG dataset (no rate limit).

    Attributes:
        max_rows: Hard cap on rows returned per query (bounds result transfer).
        per_interest_limit: Top-K matches kept per interest (batch path; mirrors DOC).
        recall_per_interest_limit: Top-K kept for the single-query recall path.
        billing_project: GCP project billed for query bytes (None → infer from creds).

    Example:
        >>> adapter = GdeltBigQueryAdapter()
        >>> # candidates = await adapter.search_active_interests(active, since_utc)
    """

    def __init__(
        self,
        billing_project: str | None = None,
        client: Any | None = None,
        max_rows: int = _DEFAULT_MAX_ROWS,
        per_interest_limit: int = _DEFAULT_PER_INTEREST_LIMIT,
        recall_per_interest_limit: int = _DEFAULT_RECALL_PER_INTEREST_LIMIT,
        body_extractor: BaseNewsAdapter | None = None,
    ) -> None:
        self.billing_project = billing_project
        self.max_rows = max(1, max_rows)
        self.per_interest_limit = max(1, per_interest_limit)
        self.recall_per_interest_limit = max(1, recall_per_interest_limit)
        # Reason: BigQuery client + body extractor are built lazily so importing /
        # unit-testing this module needs no GCP credentials nor network.
        self._client = client
        self._body_extractor = body_extractor

    # ------------------------------------------------------------------
    # Public adapter contract
    # ------------------------------------------------------------------

    async def search(
        self,
        search_query: str,
        since_utc: datetime,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[CandidateStory]:
        """Run one GKG query for a single free-text query (interest-agnostic).

        Mirrors the DOC adapter's ``search``: returns metadata-only candidates and
        does NOT stamp an interest (the pipeline / caller does). Recall-oriented
        (keeps event words, wider per-interest cap) — used for the ABC contract and
        by the Phase-2c coverage census.

        Args:
            search_query: The free-text query.
            since_utc: Lower-bound article time (also fixes the partition floor).
            domains: Optional curated authority-domain set (M4). When given, the
                query is restricted to those outlets via the additive
                ``LOWER(SourceCommonName) IN UNNEST(@domains)`` predicate.

        Raises:
            AdapterFetchError: On any BigQuery error.
        """
        tokens = recall_terms(search_query)
        if not tokens:
            return []
        # Reason: the recall/census path stays deliberately broad — single-word terms,
        # event words kept, no title gate — so ``requires_title`` is uniformly False.
        # It still carries the field so every struct in @interest_terms is homogeneous.
        terms: list[dict[str, Any]] = [
            {
                "interest_id": "__query__",
                "interest_slug": "__query__",
                "term": _term_regex(t),
                "requires_title": False,
            }
            for t in tokens
        ]
        rows = await self._run_query(
            terms,
            since_utc,
            self.recall_per_interest_limit,
            f"search:{search_query[:60]}",
            domains=domains,
        )
        return self._rows_to_candidates(rows, stamp_interest=False)

    async def search_active_interests(
        self,
        active_interests: list[ActiveInterest],
        since_utc: datetime,
        *,
        domains: list[str] | None = None,
    ) -> list[CandidateStory]:
        """Run ONE GKG query for the whole active-interest set (the batched win).

        Each returned article is emitted once per interest it matched, with
        ``candidate_matched_interest_id`` / ``_slug`` stamped — so the existing
        clusterer dedups + merges interest tags exactly as for the DOC fan-out.
        Per-interest results are ranked by distinct-terms-matched then recency and
        capped to ``per_interest_limit``.

        Args:
            active_interests: The active-interest set to ingest for.
            since_utc: Lower-bound article time (also fixes the partition floor).

        Returns:
            Interest-stamped candidates (one per matched (article, interest) pair).

        Raises:
            AdapterFetchError: On any BigQuery error (caller treats as batch failure).
        """
        # Reason: function-local import breaks the one-directional cycle — interest_lexical
        # imports the shared tokenizer/stopword sets from THIS module at load time, so we
        # pull derive_anchor_specs back in only at call time.
        from agents.ingestion.interest_lexical import derive_anchor_specs

        terms: list[dict[str, Any]] = []
        used_interests = 0
        for interest in active_interests:
            derivation = derive_anchor_specs(interest.interest_search_query)
            specs = derivation.specs
            if not specs:
                if derivation.banned_anchors:
                    # Term-hygiene gap (criterion 5): every anchor is a banned standalone
                    # generic, so the interest can match NOTHING — surface it, never a
                    # silent unmatchable interest (Rule 12).
                    logger.warning(
                        "interest_term_hygiene_gap",
                        hygiene_gap_kind="all_anchors_banned",
                        interest_slug=interest.interest_slug,
                        banned_anchors=derivation.banned_anchors,
                        usable_anchors=0,
                        fix_suggestion="Every anchor for this interest is a banned "
                        "standalone generic (never admits alone) — add a specific "
                        "multi-word or entity anchor to interest_search_query",
                    )
                else:
                    logger.warning(
                        "gdelt_bq_interest_no_terms",
                        interest_slug=interest.interest_slug,
                        fix_suggestion="interest_search_query had no usable terms; skipped this run",
                    )
                continue
            if derivation.has_uncommaed_soup_query:
                # Term-hygiene gap (issue #69): the query is a comma-less keyword list, so
                # its single anchor demands the whole sentence CONTIGUOUSLY and matches
                # nothing — invisible to has_hygiene_gap (the mega-anchor looks strong),
                # which is how the 2026-07-25 run returned 0 rows in silence. Surface it;
                # the anchors are still enqueued (the guard diagnoses, never drops).
                logger.warning(
                    "interest_term_hygiene_gap",
                    hygiene_gap_kind="uncommaed_soup_query",
                    interest_slug=interest.interest_slug,
                    usable_anchors=len(specs),
                    anchor_phrases=[spec.anchor_phrase for spec in specs],
                    fix_suggestion="interest_search_query is comma-less keyword soup — "
                    "its one anchor must match every word contiguously, so it ingests "
                    "nothing. Rewrite it as comma-separated anchor phrases "
                    "('humanoid robots, robotics') — see scripts/seed_catalog/"
                    "backfill_anchor_queries.py",
                )
            if derivation.has_hygiene_gap:
                # Term-hygiene gap (criterion 5): the interest still has anchors but every
                # usable one is short/ambiguous (title-only), so it can match nothing on
                # the story body/entities — surface it while still enqueuing the anchors.
                logger.warning(
                    "interest_term_hygiene_gap",
                    hygiene_gap_kind="no_strong_anchor",
                    interest_slug=interest.interest_slug,
                    banned_anchors=derivation.banned_anchors,
                    usable_anchors=len(specs),
                    strong_anchors=0,
                    fix_suggestion="Every usable anchor is short/ambiguous and only "
                    "matches in the title — add a specific multi-word or entity anchor "
                    "so this interest can match on the story body/entities too",
                )
            used_interests += 1
            for spec in specs:
                terms.append(
                    {
                        "interest_id": interest.interest_id,
                        "interest_slug": interest.interest_slug,
                        "term": spec.anchor_regex,
                        "requires_title": spec.requires_title,
                    }
                )
        if not terms:
            return []

        logger.info(
            "gdelt_bq_search_started",
            active_interests=len(active_interests),
            used_interests=used_interests,
            term_predicates=len(terms),
        )
        # Reason: the SQL's global ``LIMIT @max_rows`` is applied AFTER the per-interest
        # ``rn <= @per_interest_limit`` window and ORDERs BY interest_id, so a fixed cap
        # below the batch's legitimate ceiling would silently starve whole late-ordered
        # interests once used_interests × per_interest_limit exceeds it. Size the cap to
        # that exact ceiling (default self.max_rows as a floor) so every active interest
        # gets its full per-interest quota no matter how large the active set grows.
        batch_max_rows = max(self.max_rows, used_interests * self.per_interest_limit)
        rows = await self._run_query(
            terms,
            since_utc,
            self.per_interest_limit,
            f"{used_interests} interests",
            domains=domains,
            max_rows=batch_max_rows,
        )
        candidates = self._rows_to_candidates(rows, stamp_interest=True)
        logger.info(
            "gdelt_bq_search_completed",
            used_interests=used_interests,
            rows_returned=len(rows),
            candidates_emitted=len(candidates),
        )
        return candidates

    async def extract_body(
        self, candidate: CandidateStory, **kwargs: Any
    ) -> CandidateStory:
        """Fetch + extract the article body (delegated to the DOC adapter's logic).

        Body extraction is source-agnostic (HTTP GET the URL + ``trafilatura``), so
        it reuses the tested ``GdeltDocAdapter.extract_body`` rather than duplicating
        it. Never raises (the delegate swallows fetch/extract failures).
        """
        if self._body_extractor is None:
            self._body_extractor = GdeltDocAdapter()
        return await self._body_extractor.extract_body(candidate, **kwargs)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client(self) -> Any:
        """Build (once) and return the BigQuery client (creds via ADC / key env)."""
        if self._client is None:
            from google.cloud import bigquery  # local import: keep GCP optional

            self._client = bigquery.Client(project=self.billing_project)
        return self._client

    async def _run_query(
        self,
        terms: list[dict[str, str]],
        since_utc: datetime,
        per_interest_limit: int,
        label: str,
        domains: list[str] | None = None,
        max_rows: int | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the batched GKG query off the event loop; return raw row dicts.

        When ``domains`` is supplied the SQL gains the additive
        ``LOWER(SourceCommonName) IN UNNEST(@domains)`` predicate and a bound
        lowercased STRING array; without it the SQL is unchanged (additive path).

        Raises:
            AdapterFetchError: On any BigQuery / Google API error.
        """
        # Reason: the curated domains arrive lowercase (SP1) but lowercase again
        # defensively so the @domains array always matches LOWER(SourceCommonName).
        lowered_domains = [d.lower() for d in domains] if domains else None
        sql = _batch_sql_with_domains(lowered_domains)
        since = (
            since_utc if since_utc.tzinfo else since_utc.replace(tzinfo=timezone.utc)
        )
        # Partitions are midnight-granular — floor to the start of since's day so the
        # whole day's partition is included; DATE then filters precisely within it.
        since_partition = since.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        since_date = int(since.astimezone(timezone.utc).strftime(_GKG_DATE_FORMAT))

        def _execute() -> list[dict[str, Any]]:
            from google.cloud import bigquery

            term_structs = [
                bigquery.StructQueryParameter(
                    None,
                    bigquery.ScalarQueryParameter(
                        "interest_id", "STRING", t["interest_id"]
                    ),
                    bigquery.ScalarQueryParameter(
                        "interest_slug", "STRING", t["interest_slug"]
                    ),
                    bigquery.ScalarQueryParameter("term", "STRING", t["term"]),
                    # Reason: the lexical key — a short/ambiguous anchor only counts on
                    # a title match (the JOIN's ``NOT t.requires_title OR ...`` clause).
                    bigquery.ScalarQueryParameter(
                        "requires_title", "BOOL", t["requires_title"]
                    ),
                )
                for t in terms
            ]
            query_parameters = [
                bigquery.ArrayQueryParameter("interest_terms", "STRUCT", term_structs),
                bigquery.ScalarQueryParameter(
                    "since_partition", "TIMESTAMP", since_partition
                ),
                bigquery.ScalarQueryParameter("since_date", "INT64", since_date),
                bigquery.ScalarQueryParameter(
                    "per_interest_limit", "INT64", per_interest_limit
                ),
                bigquery.ScalarQueryParameter(
                    "max_rows",
                    "INT64",
                    max_rows if max_rows is not None else self.max_rows,
                ),
            ]
            # Reason: bind the @domains array only when the predicate is present, so
            # the no-domains job config is byte-identical to today's (additive path).
            if lowered_domains:
                query_parameters.append(
                    bigquery.ArrayQueryParameter("domains", "STRING", lowered_domains)
                )
            job_config = bigquery.QueryJobConfig(query_parameters=query_parameters)
            job = self._get_client().query(sql, job_config=job_config)
            return [dict(row.items()) for row in job.result()]

        try:
            return await asyncio.to_thread(_execute)
        except Exception as exc:  # noqa: BLE001 — normalize all BQ errors to the adapter error
            logger.warning(
                "gdelt_bq_query_failed",
                label=label,
                error_type=type(exc).__name__,
                error_message=str(exc)[:300],
                fix_suggestion="Check BigQuery creds (GOOGLE_APPLICATION_CREDENTIALS), the API is enabled, and quota",
            )
            raise AdapterFetchError(
                message=f"GDELT BigQuery query failed: {type(exc).__name__}",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Check BigQuery credentials, that the API is enabled, and the billing project",
            ) from exc

    def _rows_to_candidates(
        self, rows: list[dict[str, Any]], stamp_interest: bool
    ) -> list[CandidateStory]:
        """Map GKG rows → CandidateStory list (one per row; rows are per-interest).

        Also the English-only gate's Python half (#63): every row is put through
        ``evaluate_language_admission``, which re-checks GDELT's source-language stamp
        (the ``_BATCH_SQL`` half) AND adds the title-side backstop the SQL cannot do —
        so a foreign-language page that GDELT filed in its English feed is still
        rejected. Rejections and blind (no-evidence) admissions are logged in
        AGGREGATE, once per call: per-row logging of a 5000-row batch would bury the
        signal, but a silent filter would violate Rule 12.
        """
        candidates: list[CandidateStory] = []
        rejected_by_reason: dict[str, int] = {}
        blind_admissions = 0
        titles_cleaned = 0
        first_cleaned_example: tuple[str, str] | None = None
        for row in rows:
            url = (row.get("url") or "").strip()
            title = (row.get("title") or "").strip()
            domain = (row.get("outlet") or "").strip().lower()
            if not url or not title or not domain:
                continue
            language_verdict = evaluate_language_admission(
                title, row.get("translation_info")
            )
            if not language_verdict.is_admitted:
                reason = language_verdict.rejection_reason or "unknown"
                rejected_by_reason[reason] = rejected_by_reason.get(reason, 0) + 1
                continue
            if language_verdict.language_evidence == LANGUAGE_EVIDENCE_NONE:
                blind_admissions += 1
            published_utc = self._parse_gkg_date(row.get("gkg_date"))
            social_image = (row.get("sharing_image") or None) or None
            candidate_themes = _parse_v2_themes(row.get("v2_themes"))

            matched_interest_id: str | None = None
            matched_interest_slug: str | None = None
            if stamp_interest:
                interest_id = row.get("interest_id")
                # Reason: the recall/search path uses the sentinel "__query__" id —
                # never stamp it onto a candidate.
                if interest_id and interest_id != "__query__":
                    matched_interest_id = interest_id
                    matched_interest_slug = row.get("interest_slug")

            # Reason (#71): clean AFTER the language verdict, never before. The gate
            # entity-decodes internally for detection, so cleaning first would stack a
            # second decode on a double-escaped title AND delete the outlet suffix that
            # is sometimes the row's only non-Latin evidence ("- CFi.CN 中财网").
            clean_title = clean_candidate_title(
                title, outlet_name=domain, outlet_domain=domain
            )
            if clean_title != title:
                titles_cleaned += 1
                first_cleaned_example = first_cleaned_example or (title, clean_title)
            candidates.append(
                CandidateStory(
                    candidate_external_id=url,
                    candidate_title=clean_title,
                    candidate_url=url,
                    candidate_outlet_domain=domain,
                    candidate_outlet_name=domain,
                    candidate_published_utc=published_utc,
                    candidate_language=language_verdict.source_language_code,
                    candidate_social_image_url=social_image,
                    candidate_matched_interest_id=matched_interest_id,
                    candidate_matched_interest_slug=matched_interest_slug,
                    candidate_themes=candidate_themes,
                )
            )
        if rejected_by_reason:
            logger.info(
                "gdelt_non_english_candidates_rejected",
                rows_in=len(rows),
                rejected_total=sum(rejected_by_reason.values()),
                rejected_by_reason=rejected_by_reason,
                candidates_admitted=len(candidates),
            )
        if first_cleaned_example:
            # Reason (Rule 12, #71): title hygiene REWRITES what the founder reviews and
            # what persists as canonical_title, so it must never be invisible. One
            # aggregate line per call, with a worked example, is enough to spot an
            # over-eager strip in the run log without burying a 5000-row batch.
            logger.info(
                "gdelt_candidate_titles_cleaned",
                rows_in=len(rows),
                titles_cleaned=titles_cleaned,
                example_raw_title=first_cleaned_example[0][:160],
                example_clean_title=first_cleaned_example[1][:160],
            )
        if blind_admissions:
            # Reason (Rule 12): these rows carried NO language evidence either way —
            # no GDELT stamp and no English function words — so the gate failed OPEN
            # and admitted them. That is deliberate (over-rejection silently starves
            # the pool), but it must be countable, never invisible.
            logger.warning(
                "gdelt_language_admission_blind",
                rows_in=len(rows),
                blind_admissions=blind_admissions,
                fix_suggestion="Admitted with no language evidence (no GDELT "
                "TranslationInfo stamp, no English function words in the title) — "
                "fail-open by design; if junk reaches the shortlist, widen "
                "candidate_language._FOREIGN_FUNCTION_WORDS from the rejection log",
            )
        return candidates

    @staticmethod
    def _parse_gkg_date(gkg_date: Any) -> datetime:
        """Parse a GKG ``DATE`` INT64 (YYYYMMDDHHMMSS) into a UTC datetime.

        Falls back to now (UTC) on a missing/malformed value so a row with a bad
        timestamp is still ingestible (freshness just reads low) — matches the DOC
        adapter's seendate behavior.
        """
        if gkg_date is not None:
            try:
                return datetime.strptime(str(gkg_date), _GKG_DATE_FORMAT).replace(
                    tzinfo=timezone.utc
                )
            except ValueError:
                logger.warning(
                    "gdelt_bq_bad_date",
                    gkg_date=str(gkg_date),
                    fix_suggestion="Expected YYYYMMDDHHMMSS; using now() as fallback",
                )
        return datetime.now(timezone.utc)
