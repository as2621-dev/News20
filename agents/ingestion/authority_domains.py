"""Curated authority-domain set per feed category (FSR-M4 SP1) — which outlets to FETCH.

M4 rekeys news ingestion from narrow interest keywords to **category + a curated
authority-domain set**: for each of the 8 ``TOPIC_CATEGORIES`` roots
(``agents/pipeline/categories.py``) this module pins an ordered list of bare outlet
hosts the fetch trusts. The DOC ``domainis:`` builder (SP2) and the GKG
``SourceCommonName ∈ set`` predicate (SP3) consume these; SP4 keys one fetch cell per
(category, domain-set).

Domains are stored **lowercase, bare-host** (no scheme, no path) — the exact form
GDELT emits: ``gdelt_doc._article_to_candidate`` lowercases ``domain`` and the GKG
query aliases ``outlet = LOWER(SourceCommonName)``. So a stored ``reuters.com`` lines
up with what both sources return, no normalization at fetch time.

This is **separate** from M3's source-tier *authority weights* (how much an outlet's
authority LIFTS importance). M4 = which domains to fetch; M3 = how much each lifts.
They are related but deliberately kept in distinct files to avoid a cross-milestone
file-collision; a future consolidation is flagged in
``plans/phase-fsr-m4-trusted-outlet-fetch.md`` (Open questions) — not done here.

The set is a defensible **launch seed**, not the final editorial curation (the
authoritative per-category list + bias diversity is a content-ops tuning task, per the
PRD open items). It is pure data + one pure accessor — no DB, no clock, no network.
"""

from __future__ import annotations

from agents.pipeline.categories import FeedCategory

# Reason: per-category authority outlets the fetch trusts, bare-host lowercase (the
# form GDELT's ``domain`` / ``LOWER(SourceCommonName)`` emits). Ordered roughly by
# how central each outlet is to that beat — the DOC builder preserves order, so the
# first domains lead the OR-clause. A launch seed (~10–15 each); content-ops tunes it.
_AUTHORITY_DOMAINS: dict[FeedCategory, list[str]] = {
    "ai": [
        "technologyreview.com",
        "wired.com",
        "theverge.com",
        "venturebeat.com",
        "techcrunch.com",
        "arstechnica.com",
        "spectrum.ieee.org",
        "theinformation.com",
        "semafor.com",
        "reuters.com",
        "ft.com",
        "nature.com",
    ],
    "geopolitics": [
        "reuters.com",
        "apnews.com",
        "bbc.co.uk",
        "aljazeera.com",
        "ft.com",
        "economist.com",
        "foreignpolicy.com",
        "theguardian.com",
        "nytimes.com",
        "washingtonpost.com",
        "wsj.com",
        "bloomberg.com",
    ],
    "business": [
        "reuters.com",
        "bloomberg.com",
        "wsj.com",
        "ft.com",
        "cnbc.com",
        "economist.com",
        "marketwatch.com",
        "forbes.com",
        "businessinsider.com",
        "fortune.com",
        "apnews.com",
        "barrons.com",
    ],
    "environment": [
        "reuters.com",
        "apnews.com",
        "bbc.co.uk",
        "theguardian.com",
        "nationalgeographic.com",
        "scientificamerican.com",
        "insideclimatenews.org",
        "grist.org",
        "carbonbrief.org",
        "nature.com",
        "yaleclimateconnections.org",
        "eenews.net",
    ],
    "politics": [
        "reuters.com",
        "apnews.com",
        "politico.com",
        "thehill.com",
        "npr.org",
        "bbc.co.uk",
        "nytimes.com",
        "washingtonpost.com",
        "axios.com",
        "theguardian.com",
        "wsj.com",
        "cnn.com",
    ],
    "tech": [
        "theverge.com",
        "arstechnica.com",
        "techcrunch.com",
        "wired.com",
        "engadget.com",
        "cnet.com",
        "technologyreview.com",
        "thenextweb.com",
        "reuters.com",
        "bloomberg.com",
        "ft.com",
        "zdnet.com",
    ],
    "sport": [
        "espn.com",
        "bbc.co.uk",
        "skysports.com",
        "theathletic.com",
        "reuters.com",
        "apnews.com",
        "cbssports.com",
        "theguardian.com",
        "si.com",
        "nbcsports.com",
        "bleacherreport.com",
        "marca.com",
    ],
    "arts": [
        "theguardian.com",
        "nytimes.com",
        "bbc.co.uk",
        "variety.com",
        "hollywoodreporter.com",
        "rollingstone.com",
        "pitchfork.com",
        "vulture.com",
        "artnews.com",
        "npr.org",
        "newyorker.com",
        "indiewire.com",
    ],
}


def domains_for_category(category: str) -> list[str]:
    """Return the curated authority-domain list for a known feed category.

    Args:
        category: A ``TOPIC_CATEGORIES`` key (e.g. ``"business"``).

    Returns:
        The ordered, lowercase bare-host domain list for that category (a copy, so
        callers cannot mutate the module data).

    Raises:
        KeyError: If ``category`` is not a curated category — fail loud rather than
            silently returning ``[]`` (an empty set would make the fetch un-scoped /
            empty, exactly the M4 failure mode the lint test guards).

    Example:
        >>> "reuters.com" in domains_for_category("business")
        True
    """
    if category not in _AUTHORITY_DOMAINS:
        raise KeyError(
            f"no curated authority-domain set for category {category!r}; "
            f"known categories: {sorted(_AUTHORITY_DOMAINS)}"
        )
    return list(_AUTHORITY_DOMAINS[category])
