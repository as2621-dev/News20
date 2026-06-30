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

**Matching (precision + relevance ranking).** An interest's free-text
``interest_search_query`` is tokenized into *anchor terms* (dropping stopwords and
content-free event words). A GKG row matches an interest if its
``title + V2Persons + V2Organizations + V2Locations`` haystack contains ANY anchor
term (word-boundary regex). To suppress broad-term noise WITHOUT hard exclusions,
each interest's matches are ranked by the **number of distinct anchor terms matched**
(then recency) and capped to the top ``per_interest_limit`` (default 75, mirroring
the DOC ``maxrecords``). So for "India cricket … BCCI", an India+cricket article
(2 terms) outranks an India-politics article (1 term), and the cap bounds the pool
so the O(n²) clusterer stays fast. (Chosen over a hard ``country AND topic`` rule,
which over-excludes e.g. "Israel Gaza ceasefire" articles lacking the word
"ceasefire".) No interest→entity link exists in the schema, so matching is
query-driven; the entity registry stays a ranking-time concern.

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


# Reason: one parameterized query — interest terms arrive as a STRUCT array
# (@interest_terms, one row per (interest, term)), so the SQL is fixed and
# injection-safe regardless of interest count. A row matches an interest if ANY
# term hits its entity+title haystack; matches are GROUPed per (article, interest)
# to count distinct matched terms. Ranking puts TITLE matches first
# (title_match_count) — a term in the headline means the story is *about* the
# interest, vs an incidental body/entity-tag mention (which made multi-country
# roundups outrank focused stories) — then total match_count, then recency.
_BATCH_SQL = r"""
WITH raw AS (
  SELECT
    DocumentIdentifier AS url,
    LOWER(SourceCommonName) AS outlet,
    DATE AS gkg_date,
    NULLIF(SharingImage, '') AS sharing_image,
    REGEXP_EXTRACT(Extras, r'<PAGE_TITLE>(.*?)</PAGE_TITLE>') AS title,
    V2Persons, V2Organizations, V2Locations, V2Themes
  FROM `gdelt-bq.gdeltv2.gkg_partitioned`
  WHERE _PARTITIONTIME >= @since_partition
    AND DATE >= @since_date
    AND DocumentIdentifier IS NOT NULL AND DocumentIdentifier != ''
    AND SourceCommonName IS NOT NULL AND SourceCommonName != ''
),
base AS (
  SELECT
    url, outlet, gkg_date, sharing_image, title, V2Themes AS v2_themes,
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
    t.interest_id, t.interest_slug,
    COUNT(DISTINCT t.term) AS match_count,
    COUNT(DISTINCT IF(REGEXP_CONTAINS(b.title_hay, t.term), t.term, NULL)) AS title_match_count
  FROM base AS b
  JOIN UNNEST(@interest_terms) AS t
    ON REGEXP_CONTAINS(b.hay, t.term)
  GROUP BY b.url, b.outlet, b.gkg_date, b.sharing_image, b.title, b.v2_themes,
           t.interest_id, t.interest_slug
),
ranked AS (
  SELECT *, ROW_NUMBER() OVER (
    PARTITION BY interest_id
    ORDER BY title_match_count DESC, match_count DESC, gkg_date DESC
  ) AS rn
  FROM matched
)
SELECT url, outlet, gkg_date, sharing_image, title, v2_themes,
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
        terms = [
            {
                "interest_id": "__query__",
                "interest_slug": "__query__",
                "term": _term_regex(t),
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
        terms: list[dict[str, str]] = []
        used_interests = 0
        for interest in active_interests:
            tokens = match_terms(interest.interest_search_query)
            if not tokens:
                logger.warning(
                    "gdelt_bq_interest_no_terms",
                    interest_slug=interest.interest_slug,
                    fix_suggestion="interest_search_query had no usable terms; skipped this run",
                )
                continue
            used_interests += 1
            for token in tokens:
                terms.append(
                    {
                        "interest_id": interest.interest_id,
                        "interest_slug": interest.interest_slug,
                        "term": _term_regex(token),
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
        rows = await self._run_query(
            terms,
            since_utc,
            self.per_interest_limit,
            f"{used_interests} interests",
            domains=domains,
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
        lowered_domains = (
            [d.lower() for d in domains] if domains else None
        )
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
                )
                for t in terms
            ]
            query_parameters = [
                bigquery.ArrayQueryParameter(
                    "interest_terms", "STRUCT", term_structs
                ),
                bigquery.ScalarQueryParameter(
                    "since_partition", "TIMESTAMP", since_partition
                ),
                bigquery.ScalarQueryParameter("since_date", "INT64", since_date),
                bigquery.ScalarQueryParameter(
                    "per_interest_limit", "INT64", per_interest_limit
                ),
                bigquery.ScalarQueryParameter("max_rows", "INT64", self.max_rows),
            ]
            # Reason: bind the @domains array only when the predicate is present, so
            # the no-domains job config is byte-identical to today's (additive path).
            if lowered_domains:
                query_parameters.append(
                    bigquery.ArrayQueryParameter(
                        "domains", "STRING", lowered_domains
                    )
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
        """Map GKG rows → CandidateStory list (one per row; rows are per-interest)."""
        candidates: list[CandidateStory] = []
        for row in rows:
            url = (row.get("url") or "").strip()
            title = (row.get("title") or "").strip()
            domain = (row.get("outlet") or "").strip().lower()
            if not url or not title or not domain:
                continue
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

            candidates.append(
                CandidateStory(
                    candidate_external_id=url,
                    candidate_title=title,
                    candidate_url=url,
                    candidate_outlet_domain=domain,
                    candidate_outlet_name=domain,
                    candidate_published_utc=published_utc,
                    candidate_social_image_url=social_image,
                    candidate_matched_interest_id=matched_interest_id,
                    candidate_matched_interest_slug=matched_interest_slug,
                    candidate_themes=candidate_themes,
                )
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
