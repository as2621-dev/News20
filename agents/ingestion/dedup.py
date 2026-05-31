"""Cross-outlet story clustering + deduplication for the News20 ingestion pool.

The URL-normalization and title-similarity *primitives* are ported verbatim from
the TLDW donor (`agents/ingestion/dedup.py`, reference/reuse-map.md "Ingestion" =
PORT) — they are well-tested and source-agnostic. The *batch* behaviour is
**adapted**: TLDW's ``deduplicate_batch`` merely *drops* duplicates, but News20
needs to *cluster* near-duplicate articles from many outlets into a single
``CanonicalStory`` and **count the distinct covering outlets** — that count is the
"N outlets covering this" trust/coverage number (reference/reuse-map.md Decision
#6). Clustering is global across interests (not segment-scoped like the donor), so
one event surfaced by both an "Arsenal" and a "Soccer" query collapses to one
story carrying both matched interests.

Pure stdlib (difflib, urllib.parse, re, hashlib) — no external dependencies.

Example:
    >>> from agents.ingestion.dedup import StoryClusterer
    >>> clusterer = StoryClusterer()
    >>> canonical = clusterer.cluster_candidates(candidates)
    >>> canonical[0].story_outlet_count
    3
"""

from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlencode, urlparse

from agents.ingestion.models import CandidateStory, CanonicalStory
from agents.shared.logger import get_logger

logger = get_logger(__name__)

# Reason: Common tracking/attribution query parameters injected by ad platforms,
# email marketing tools, and social sharing. They do not affect the canonical
# identity of a URL and must be stripped for accurate dedup. (PORT from TLDW.)
TRACKING_PARAMS: frozenset[str] = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "ref",
        "source",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "s",
        "via",
    }
)

_DEFAULT_TITLE_THRESHOLD = 0.85


def normalize_url(url: str) -> str:
    """Normalize a URL for deduplication comparison (PORT from TLDW).

    Strips tracking query params, forces https, removes a leading ``www.``,
    drops a trailing slash. Returns "" for empty input.

    Args:
        url: The raw URL to normalize.

    Returns:
        The normalized URL string.

    Example:
        >>> normalize_url("http://www.example.com/article/?utm_source=x")
        'https://example.com/article'
    """
    if not url:
        return ""

    parsed = urlparse(url)
    scheme = "https"
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")

    params = {
        key: value
        for key, value in parse_qs(parsed.query).items()
        if key.lower() not in TRACKING_PARAMS
    }
    clean_query = urlencode(params, doseq=True) if params else ""

    return f"{scheme}://{netloc}{path}{'?' + clean_query if clean_query else ''}"


def compute_title_similarity(title_a: str, title_b: str) -> float:
    """Compute title similarity using SequenceMatcher, 0.0–1.0 (PORT from TLDW).

    Lowercases and strips punctuation before comparison so minor formatting
    differences ("AI Model Released!" vs "AI Model Released") do not count as
    different. Returns 0.0 if either title is empty after cleaning.

    Args:
        title_a: First title string.
        title_b: Second title string.

    Returns:
        Similarity ratio between 0.0 (different) and 1.0 (identical).

    Example:
        >>> compute_title_similarity("Breaking: AI Released", "Breaking AI Released!") > 0.85
        True
    """
    clean_a = re.sub(r"[^\w\s]", "", title_a.lower()).strip()
    clean_b = re.sub(r"[^\w\s]", "", title_b.lower()).strip()

    if not clean_a or not clean_b:
        return 0.0

    return SequenceMatcher(None, clean_a, clean_b).ratio()


def provisional_story_id(normalized_url: str) -> str:
    """Derive a deterministic provisional story id from a normalized URL.

    The real ``stories.story_id`` (slug/uuid) is assigned at persist time (SP3);
    this id is stable per cluster so SP1 outputs (and tests) are deterministic.

    Args:
        normalized_url: The cluster key (normalized representative URL).

    Returns:
        A short stable id like ``cand-1a2b3c4d5e6f``.

    Example:
        >>> provisional_story_id("https://cnn.com/x") == provisional_story_id("https://cnn.com/x")
        True
    """
    digest = hashlib.sha1(normalized_url.encode("utf-8")).hexdigest()[:12]
    return f"cand-{digest}"


class _StoryCluster:
    """Mutable accumulator for one cluster of near-duplicate candidates."""

    def __init__(self, normalized_url: str, first: CandidateStory) -> None:
        self.normalized_url = normalized_url
        self.members: list[CandidateStory] = [first]

    def add(self, candidate: CandidateStory) -> None:
        self.members.append(candidate)

    @property
    def representative(self) -> CandidateStory:
        """The earliest-published member (ties broken by first-seen order)."""
        return min(
            self.members,
            key=lambda member: member.candidate_published_utc,
        )


class StoryClusterer:
    """Clusters near-duplicate candidates into canonical stories with outlet counts.

    Two candidates join the same cluster when their normalized URLs are equal, or
    their titles are similar at/above ``title_threshold``. Each cluster yields one
    CanonicalStory whose ``story_outlet_count`` is the number of distinct covering
    outlet domains.

    Attributes:
        title_threshold: Minimum SequenceMatcher ratio to treat titles as the
            same story.

    Example:
        >>> clusterer = StoryClusterer(title_threshold=0.9)
        >>> canonical = clusterer.cluster_candidates(candidates)
    """

    def __init__(self, title_threshold: float = _DEFAULT_TITLE_THRESHOLD) -> None:
        self.title_threshold = title_threshold

    def cluster_candidates(
        self, candidates: list[CandidateStory]
    ) -> list[CanonicalStory]:
        """Group candidates into canonical stories with distinct outlet counts.

        Args:
            candidates: Raw fetched candidates (across all interests).

        Returns:
            One CanonicalStory per detected real-world story.

        Example:
            >>> StoryClusterer().cluster_candidates([])
            []
        """
        clusters: list[_StoryCluster] = []
        url_index: dict[str, _StoryCluster] = {}

        for candidate in candidates:
            normalized = normalize_url(candidate.candidate_url)
            matched = self._find_cluster(candidate, normalized, url_index, clusters)
            if matched is not None:
                matched.add(candidate)
                continue
            new_cluster = _StoryCluster(normalized, candidate)
            clusters.append(new_cluster)
            if normalized:
                url_index[normalized] = new_cluster

        canonical = [self._to_canonical(cluster) for cluster in clusters]
        logger.info(
            "story_clustering_completed",
            total_candidates=len(candidates),
            canonical_stories=len(canonical),
            multi_outlet_stories=sum(1 for c in canonical if c.story_outlet_count > 1),
        )
        return canonical

    def _find_cluster(
        self,
        candidate: CandidateStory,
        normalized: str,
        url_index: dict[str, _StoryCluster],
        clusters: list[_StoryCluster],
    ) -> _StoryCluster | None:
        """Find an existing cluster matching by normalized URL or title similarity."""
        # --- Strategy 1: exact normalized-URL match (O(1)) ---
        if normalized and normalized in url_index:
            return url_index[normalized]

        # --- Strategy 2: fuzzy title match against each cluster's representative ---
        for cluster in clusters:
            similarity = compute_title_similarity(
                candidate.candidate_title, cluster.representative.candidate_title
            )
            if similarity >= self.title_threshold:
                logger.debug(
                    "story_cluster_title_match",
                    title_a=candidate.candidate_title[:120],
                    title_b=cluster.representative.candidate_title[:120],
                    similarity_score=round(similarity, 4),
                )
                return cluster
        return None

    def _to_canonical(self, cluster: _StoryCluster) -> CanonicalStory:
        """Collapse a cluster into a CanonicalStory with distinct outlet count."""
        representative = cluster.representative
        normalized = normalize_url(representative.candidate_url)

        # Reason: distinct covering outlets = the trust/coverage number (Decision #6).
        covering_outlets = sorted(
            {
                member.candidate_outlet_domain
                for member in cluster.members
                if member.candidate_outlet_domain
            }
        )
        matched_interest_ids = sorted(
            {
                member.candidate_matched_interest_id
                for member in cluster.members
                if member.candidate_matched_interest_id
            }
        )
        social_image = next(
            (
                m.candidate_social_image_url
                for m in cluster.members
                if m.candidate_social_image_url
            ),
            None,
        )
        body_text = next(
            (m.candidate_body_text for m in cluster.members if m.candidate_body_text),
            None,
        )

        return CanonicalStory(
            canonical_story_id=provisional_story_id(
                normalized or representative.candidate_url
            ),
            canonical_title=representative.candidate_title,
            canonical_url=representative.candidate_url,
            canonical_normalized_url=normalized,
            canonical_published_utc=representative.candidate_published_utc,
            canonical_primary_outlet_domain=representative.candidate_outlet_domain,
            canonical_primary_outlet_name=representative.candidate_outlet_name
            or representative.candidate_outlet_domain,
            canonical_social_image_url=social_image,
            canonical_body_text=body_text,
            canonical_representative_external_id=representative.candidate_external_id,
            covering_outlets=covering_outlets,
            story_outlet_count=len(covering_outlets),
            canonical_matched_interest_ids=matched_interest_ids,
            member_candidate_ids=[m.candidate_external_id for m in cluster.members],
        )
