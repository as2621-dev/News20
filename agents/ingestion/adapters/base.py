"""Abstract base class for News20 news source adapters.

Adapted from the TLDW donor (`agents/ingestion/adapters/base.py`, reference/
reuse-map.md "Ingestion" = PORT) — the two-phase contract is preserved, but
retargeted from TLDW's per-user content items to News20's interest-keyed shared
stories: ``search()`` discovers candidate articles for a news query, and
``extract_body()`` enriches one candidate with its full body text.

Decoupling the pipeline from source-specific API details makes it easy to add
new news sources (GDELT today; RSS/NewsAPI/HN later) behind one interface.

Example:
    >>> class MyAdapter(BaseNewsAdapter):
    ...     async def search(self, search_query, since_utc, **kwargs):
    ...         ...
    ...     async def extract_body(self, candidate, **kwargs):
    ...         ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from agents.ingestion.models import CandidateStory


class BaseNewsAdapter(ABC):
    """Abstract base adapter that all News20 news-source adapters must implement.

    Defines the two-phase contract for interest-keyed ingestion:
        1. search()        -- discover candidate articles matching a news query
        2. extract_body()  -- fetch + extract the full body text for one candidate

    Subclasses implement both with source-specific logic. The adapter is
    *interest-agnostic*: it knows nothing about which interest a query came from
    (the pipeline stamps ``candidate_matched_interest_id`` after fetch).
    """

    @abstractmethod
    async def search(
        self,
        search_query: str,
        since_utc: datetime,
        **kwargs: Any,
    ) -> list[CandidateStory]:
        """Discover candidate articles matching a news query since a cutoff.

        Queries the source's API/feed and returns lightweight CandidateStory
        instances with metadata populated (title, url, outlet domain, published
        time) but ``candidate_body_text`` left None (filled by extract_body).

        Args:
            search_query: The news query string (an interest's search query).
            since_utc: Only return articles published after this UTC timestamp.
            **kwargs: Adapter-specific parameters (e.g., max_records).

        Returns:
            A list of CandidateStory instances with metadata fields populated.

        Raises:
            AdapterFetchError: When the source API errors, times out, or returns
                a non-parseable response.
        """
        ...

    @abstractmethod
    async def extract_body(
        self,
        candidate: CandidateStory,
        **kwargs: Any,
    ) -> CandidateStory:
        """Fetch and extract the full article body text for one candidate.

        Takes a metadata-only CandidateStory, fetches its URL, extracts the
        readable article body, and returns the same model with
        ``candidate_body_text`` populated. On extraction failure the body is
        left None (the candidate is still usable for clustering / counts) — body
        extraction never raises, so one bad article cannot fail the batch.

        Args:
            candidate: The candidate to enrich (``candidate_body_text`` is None).
            **kwargs: Adapter-specific parameters.

        Returns:
            The candidate with ``candidate_body_text`` populated (or still None
            if extraction failed).
        """
        ...
