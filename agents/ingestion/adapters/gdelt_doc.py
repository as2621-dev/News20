"""GDELT DOC 2.0 news adapter — the News20 v1 ingestion source.

GDELT DOC 2.0 (https://api.gdeltproject.org/api/v2/doc/doc) is a **keyless**,
global, near-real-time article index. It was chosen over NewsAPI because no
NewsAPI key is available, and validated live on 2026-05-31 (see
plans/phase-1d-daily-content-pipeline-progress.md Step 0). Findings baked in here:

  • **Throttle <=1 request / 5s** — faster bursts return a plaintext rate-limit
    notice ("Please limit requests to one every 5 seconds ...") instead of JSON.
  • **sort=hybridrel** — relevance+recency blend (datedesc alone is noisy).
  • **Metadata only, no body** — GDELT returns url/domain/title/seendate; the
    article body is fetched + extracted separately via ``trafilatura``.
  • **timespan 1d–3d, maxrecords <= 250** per query.

The adapter is interest-agnostic: ``search()`` runs one news query; the pipeline
stamps which interest surfaced each result.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import trafilatura

from agents.ingestion.adapters.base import BaseNewsAdapter
from agents.ingestion.models import CandidateStory
from agents.shared.exceptions import AdapterFetchError
from agents.shared.logger import get_logger

logger = get_logger(__name__)

_ADAPTER_NAME = "gdelt_doc"
_GDELT_DOC_ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
_GDELT_USER_AGENT = "News20/1.0 (content aggregator)"

# Reason: GDELT returns a plaintext rate-limit notice (HTTP 200) instead of JSON
# when queried faster than ~once per 5 seconds. We space requests >= this gap.
_GDELT_MIN_REQUEST_INTERVAL_SECONDS = 5.0
_GDELT_MAX_RECORDS_CEILING = 250
_GDELT_RATE_LIMIT_MARKER = "Please limit requests"
_GDELT_SEENDATE_FORMAT = "%Y%m%dT%H%M%SZ"

_DEFAULT_MAX_RECORDS = 75
_DEFAULT_TIMEOUT_SECONDS = 30.0
_MIN_TIMESPAN_DAYS = 1
_MAX_TIMESPAN_DAYS = 3

# Reason: GDELT blanket-rate-limits a busy IP (HTTP 429) and also throttles bursts
# with an HTTP-200 "Please limit requests" notice. On either signal — or a transient
# transport/timeout/5xx — we back off and retry instead of failing the whole query
# (the 4a cloud cron's resilience). Base backoff >= the 5s window so the first retry
# clears a fresh slot; capped to bound the worst-case batch latency.
_GDELT_RETRY_MAX_ATTEMPTS = 3
_GDELT_RETRY_BASE_BACKOFF_SECONDS = 5.0
_GDELT_RETRY_BACKOFF_CAP_SECONDS = 30.0


def build_domain_query(
    domains: list[str], *, search_query: str | None = None
) -> str:
    """Build a GDELT DOC 2.0 trusted-domain query (a pure string function).

    DOC has no native multi-domain parameter, so a domain set is OR-composed from
    ``domainis:`` atoms: ``(domainis:reuters.com OR domainis:apnews.com OR …)``. A
    single domain emits one bare ``domainis:reuters.com`` (no needless parens). When
    a ``search_query`` (a category/keyword string) is given it is AND-composed with
    the domain clause as ``<search_query> AND <domain_clause>`` — so the fetch is
    both topic- and outlet-scoped.

    Domains are emitted **verbatim** (SP1 already normalized them to lowercase
    bare-host); this function does not re-normalize.

    Args:
        domains: The curated authority domains (must be non-empty).
        search_query: Optional topic/keyword clause to AND with the domains.

    Returns:
        The DOC query string.

    Raises:
        ValueError: If ``domains`` is empty — emitting a query with no ``domainis:``
            clause would no longer be domain-scoped and would pull random outlets,
            defeating M4's trusted-outlet fetch (fail loud, never silent).

    Example:
        >>> build_domain_query(["reuters.com", "apnews.com"])
        '(domainis:reuters.com OR domainis:apnews.com)'
        >>> build_domain_query(["reuters.com"])
        'domainis:reuters.com'
        >>> build_domain_query(["reuters.com"], search_query="climate")
        'climate AND domainis:reuters.com'
    """
    if not domains:
        raise ValueError(
            "build_domain_query requires a non-empty domain set — an empty set would "
            "emit an un-scoped query that pulls random outlets (M4 fetch must be "
            "trusted-outlet-only)"
        )
    atoms = [f"domainis:{domain}" for domain in domains]
    if len(atoms) == 1:
        domain_clause = atoms[0]
    else:
        domain_clause = "(" + " OR ".join(atoms) + ")"

    query = (search_query or "").strip()
    if query:
        return f"{query} AND {domain_clause}"
    return domain_clause


class GdeltDocAdapter(BaseNewsAdapter):
    """News adapter backed by the keyless GDELT DOC 2.0 article index.

    Attributes:
        max_records: Max articles per query (clamped to GDELT's 250 ceiling).
        min_request_interval_seconds: Minimum spacing between GDELT API calls.

    Example:
        >>> adapter = GdeltDocAdapter()
        >>> # candidates = await adapter.search("Arsenal FC", since_utc=...)
    """

    def __init__(
        self,
        http_client: httpx.AsyncClient | None = None,
        max_records: int = _DEFAULT_MAX_RECORDS,
        min_request_interval_seconds: float = _GDELT_MIN_REQUEST_INTERVAL_SECONDS,
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        retry_max_attempts: int = _GDELT_RETRY_MAX_ATTEMPTS,
        retry_base_backoff_seconds: float = _GDELT_RETRY_BASE_BACKOFF_SECONDS,
    ) -> None:
        self._http_client = http_client
        self.max_records = max(1, min(max_records, _GDELT_MAX_RECORDS_CEILING))
        self.min_request_interval_seconds = min_request_interval_seconds
        self.timeout_seconds = timeout_seconds
        # Reason: bounded backoff-retry on GDELT rate-limits / transient errors.
        # Tests inject 0.0 backoff so the retry paths run instantly.
        self.retry_max_attempts = max(1, retry_max_attempts)
        self.retry_base_backoff_seconds = retry_base_backoff_seconds
        # Reason: serialize GDELT calls so concurrent per-interest searches still
        # honor the shared 5s rate limit. monotonic() avoids wall-clock jumps.
        self._throttle_lock = asyncio.Lock()
        self._last_request_monotonic: float | None = None

    async def search(
        self,
        search_query: str,
        since_utc: datetime,
        *,
        domains: list[str] | None = None,
        **kwargs: Any,
    ) -> list[CandidateStory]:
        """Run one GDELT DOC query and parse the article list into candidates.

        Args:
            search_query: The news query string (an interest's search query).
            since_utc: Lower-bound publish time; converted to a GDELT ``timespan``
                of 1–3 days.
            domains: Optional curated authority-domain set (M4). When given, the
                outgoing query is built by ``build_domain_query`` — the
                ``search_query`` (when truthy) is AND-composed with a ``domainis:``
                clause so the fetch is outlet-scoped. When ``None`` the existing
                keyword path is used unchanged.
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            A list of metadata-only CandidateStory instances (body text None).

        Raises:
            AdapterFetchError: On HTTP error, timeout, the rate-limit notice, or
                a non-JSON response. The pipeline catches this per-interest so
                one failed query does not abort the whole batch.
            ValueError: If ``domains`` is an empty list (via ``build_domain_query``).
        """
        effective_query = (
            build_domain_query(domains, search_query=search_query)
            if domains is not None
            else search_query
        )
        timespan = self._compute_timespan(since_utc)
        params = {
            "query": effective_query,
            "mode": "ArtList",
            "format": "json",
            "sort": "hybridrel",
            "maxrecords": str(self.max_records),
            "timespan": timespan,
        }
        logger.info(
            "gdelt_search_started",
            search_query=search_query[:120],
            timespan=timespan,
            max_records=self.max_records,
        )

        raw_text = await self._throttled_get(params, search_query)
        articles = self._parse_articles(raw_text, search_query)
        candidates = [
            candidate
            for article in articles
            if (candidate := self._article_to_candidate(article)) is not None
        ]

        logger.info(
            "gdelt_search_completed",
            search_query=search_query[:120],
            articles_returned=len(articles),
            candidates_parsed=len(candidates),
        )
        return candidates

    async def extract_body(
        self,
        candidate: CandidateStory,
        **kwargs: Any,
    ) -> CandidateStory:
        """Fetch the article URL and extract its body text with trafilatura.

        Never raises: on any fetch/extract failure the candidate is returned
        unchanged (body still None) so one bad article cannot fail the batch.

        Args:
            candidate: The candidate to enrich.
            **kwargs: Unused (accepted for interface compatibility).

        Returns:
            The candidate with ``candidate_body_text`` populated, or unchanged on
            failure.
        """
        owns_client = self._http_client is None
        client = self._http_client
        try:
            if client is None:
                client = httpx.AsyncClient(
                    timeout=self.timeout_seconds, follow_redirects=True
                )
            response = await client.get(
                candidate.candidate_url, headers={"User-Agent": _GDELT_USER_AGENT}
            )
            response.raise_for_status()
            html = response.text
            # Reason: trafilatura.extract is synchronous (lxml) — run off the event loop.
            body_text = await asyncio.to_thread(trafilatura.extract, html)
            if body_text and body_text.strip():
                candidate.candidate_body_text = body_text.strip()
                logger.info(
                    "gdelt_extract_body_success",
                    candidate_url=candidate.candidate_url[:120],
                    body_chars=len(candidate.candidate_body_text),
                )
            else:
                logger.warning(
                    "gdelt_extract_body_empty",
                    candidate_url=candidate.candidate_url[:120],
                    fix_suggestion="trafilatura returned no text; article may be paywalled or JS-rendered",
                )
            return candidate
        except Exception as exc:  # noqa: BLE001 — body extraction must never fail the batch
            logger.warning(
                "gdelt_extract_body_failed",
                candidate_url=candidate.candidate_url[:120],
                error_type=type(exc).__name__,
                error_message=str(exc)[:300],
                fix_suggestion="Article fetch/extract failed; candidate kept without body text",
            )
            return candidate
        finally:
            if owns_client and client is not None:
                await client.aclose()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _compute_timespan(self, since_utc: datetime) -> str:
        """Convert a since-cutoff into a GDELT ``timespan`` string (1d–3d)."""
        now = datetime.now(timezone.utc)
        since = (
            since_utc if since_utc.tzinfo else since_utc.replace(tzinfo=timezone.utc)
        )
        elapsed_days = (now - since).total_seconds() / 86400.0
        # Reason: subtract a small epsilon before ceil so an exact N-days-ago cutoff
        # maps to "Nd" (not "N+1d") despite sub-second drift between the two now()s,
        # while still rounding any genuine fraction up so we never under-cover.
        ceil_days = math.ceil(elapsed_days - 1e-6)
        days = max(_MIN_TIMESPAN_DAYS, min(ceil_days, _MAX_TIMESPAN_DAYS))
        return f"{days}d"

    async def _throttled_get(self, params: dict[str, str], search_query: str) -> str:
        """GET the GDELT endpoint with >=5s spacing + bounded backoff-retry.

        GDELT throttles aggressively: it answers HTTP 429, or an HTTP-200 plaintext
        "Please limit requests" notice, when called faster than ~1/5s, and can
        blanket-rate-limit a busy IP (the 4a cloud run's risk). On any rate-limit
        signal (429 / the notice), a 5xx, or a transient transport/timeout error,
        we wait an exponential backoff (>= the 5s window) and retry, up to
        ``retry_max_attempts`` total. A non-retryable 4xx, or exhausted retries,
        raises AdapterFetchError (the pipeline catches it per-interest, so one
        throttled query does not abort the whole batch).

        Args:
            params: The GDELT DOC query parameters.
            search_query: The query string (for log context only).

        Returns:
            The raw response body text (parsed downstream by ``_parse_articles``).

        Raises:
            AdapterFetchError: On a non-retryable HTTP error or exhausted retries.
        """
        owns_client = self._http_client is None
        client = self._http_client
        try:
            if client is None:
                client = httpx.AsyncClient(
                    timeout=self.timeout_seconds, follow_redirects=True
                )
            last_error_summary = "unknown"
            for attempt in range(1, self.retry_max_attempts + 1):
                retry_reason: str | None = None
                async with self._throttle_lock:
                    await self._await_rate_limit()
                    try:
                        response = await client.get(
                            _GDELT_DOC_ENDPOINT,
                            params=params,
                            headers={"User-Agent": _GDELT_USER_AGENT},
                        )
                        self._last_request_monotonic = time.monotonic()
                    except (httpx.TimeoutException, httpx.TransportError) as exc:
                        # Transient network error → retryable.
                        retry_reason = type(exc).__name__
                        last_error_summary = f"{retry_reason}: {str(exc)[:200]}"
                    else:
                        status = response.status_code
                        if status == 429 or status >= 500:
                            retry_reason = f"HTTP {status}"
                            last_error_summary = retry_reason
                        elif _GDELT_RATE_LIMIT_MARKER in response.text[:200]:
                            # GDELT's HTTP-200 "Please limit requests" throttle notice.
                            retry_reason = "rate_limit_notice"
                            last_error_summary = "HTTP 200 rate-limit notice"
                        elif status >= 400:
                            # Non-retryable client error (e.g. a bad query) — fail fast.
                            logger.warning(
                                "gdelt_http_error",
                                search_query=search_query[:120],
                                status_code=status,
                                fix_suggestion="Non-retryable GDELT client error; check the query syntax",
                            )
                            raise AdapterFetchError(
                                message=f"GDELT request failed: HTTP {status}",
                                adapter_name=_ADAPTER_NAME,
                                fix_suggestion="Check the query syntax and request parameters",
                            )
                        else:
                            return response.text
                # Only reached when this attempt hit a retryable condition. Back off
                # OUTSIDE the throttle lock so other interest queries aren't blocked.
                if attempt < self.retry_max_attempts:
                    backoff_seconds = self._retry_backoff_seconds(attempt)
                    logger.warning(
                        "gdelt_rate_limited_retrying",
                        search_query=search_query[:120],
                        attempt=attempt,
                        max_attempts=self.retry_max_attempts,
                        retry_reason=retry_reason,
                        backoff_seconds=backoff_seconds,
                        fix_suggestion="GDELT throttled the request; backing off (>=5s window) and retrying",
                    )
                    await asyncio.sleep(backoff_seconds)
            # Every attempt hit a retryable condition — give up loudly (Rule 12).
            logger.warning(
                "gdelt_rate_limit_exhausted",
                search_query=search_query[:120],
                attempts=self.retry_max_attempts,
                last_error=last_error_summary,
                fix_suggestion="GDELT kept throttling; run from a fresh IP or lower the request rate",
            )
            raise AdapterFetchError(
                message=(
                    f"GDELT request failed after {self.retry_max_attempts} attempts: "
                    f"{last_error_summary}"
                ),
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="GDELT kept rate-limiting; reduce the request rate or run from a different IP",
            )
        finally:
            if owns_client and client is not None:
                await client.aclose()

    def _retry_backoff_seconds(self, attempt: int) -> float:
        """Exponential backoff (seconds) for a 1-based retry ``attempt``, capped.

        ``attempt`` 1 → base, 2 → base*2, … capped at the ceiling. The base is the
        GDELT 5s window so the first retry already clears a fresh rate-limit slot.

        Args:
            attempt: The 1-based attempt number that just failed.

        Returns:
            The seconds to sleep before the next attempt.
        """
        backoff = self.retry_base_backoff_seconds * (2 ** (attempt - 1))
        return min(backoff, _GDELT_RETRY_BACKOFF_CAP_SECONDS)

    async def _await_rate_limit(self) -> None:
        """Sleep just enough to keep GDELT calls >= the minimum interval apart."""
        if self._last_request_monotonic is None:
            return
        elapsed = time.monotonic() - self._last_request_monotonic
        remaining = self.min_request_interval_seconds - elapsed
        if remaining > 0:
            await asyncio.sleep(remaining)

    def _parse_articles(self, raw_text: str, search_query: str) -> list[dict[str, Any]]:
        """Parse the GDELT response text into the raw ``articles`` list.

        Raises AdapterFetchError on the rate-limit notice or non-JSON body.
        Returns an empty list for an empty / article-less (but valid) response.
        """
        text = raw_text.strip()
        if not text:
            return []
        if _GDELT_RATE_LIMIT_MARKER in text[:200]:
            logger.warning(
                "gdelt_rate_limited",
                search_query=search_query[:120],
                fix_suggestion="Throttle to <=1 request / 5s; the pipeline serializes GDELT calls",
            )
            raise AdapterFetchError(
                message="GDELT returned a rate-limit notice instead of JSON",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Throttle to <=1 request / 5s and retry",
            )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            logger.warning(
                "gdelt_non_json_response",
                search_query=search_query[:120],
                snippet=text[:160],
                fix_suggestion="GDELT returned a non-JSON body; check the query syntax and format=json",
            )
            raise AdapterFetchError(
                message="GDELT returned a non-JSON response body",
                adapter_name=_ADAPTER_NAME,
                fix_suggestion="Verify the query syntax and that format=json is set",
            ) from exc
        articles = payload.get("articles", [])
        return articles if isinstance(articles, list) else []

    def _article_to_candidate(self, article: dict[str, Any]) -> CandidateStory | None:
        """Map one GDELT article dict into a CandidateStory (None if unusable)."""
        url = (article.get("url") or "").strip()
        title = (article.get("title") or "").strip()
        domain = (article.get("domain") or "").strip().lower()
        if not url or not title or not domain:
            return None

        published_utc = self._parse_seendate(article.get("seendate"))
        social_image = (article.get("socialimage") or "").strip() or None

        return CandidateStory(
            candidate_external_id=url,
            candidate_title=title,
            candidate_url=url,
            candidate_outlet_domain=domain,
            candidate_outlet_name=domain,
            candidate_published_utc=published_utc,
            candidate_language=(article.get("language") or "").strip() or None,
            candidate_source_country=(article.get("sourcecountry") or "").strip()
            or None,
            candidate_social_image_url=social_image,
        )

    def _parse_seendate(self, seendate: str | None) -> datetime:
        """Parse a GDELT ``seendate`` ('YYYYMMDDTHHMMSSZ') into a UTC datetime.

        Falls back to now (UTC) if the field is missing or malformed, so a story
        with a bad timestamp is still ingestible (freshness will just read low).
        """
        if seendate:
            try:
                return datetime.strptime(
                    seendate.strip(), _GDELT_SEENDATE_FORMAT
                ).replace(tzinfo=timezone.utc)
            except ValueError:
                logger.warning(
                    "gdelt_bad_seendate",
                    seendate=seendate,
                    fix_suggestion="Expected GDELT format YYYYMMDDTHHMMSSZ; using now() as fallback",
                )
        return datetime.now(timezone.utc)
