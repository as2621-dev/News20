"""Lint/schema tests for the curated authority-domain set (FSR-M4 SP1).

These encode the catalog-quality contract: a missing category, a mistyped domain,
or an accessor that swallows an unknown key each means a category fetches from the
WRONG or ZERO outlets — the M4 coverage risk. Every assertion below fails loud on
exactly one of those breaks; none is a tautology that can't fail when the data
changes.

    >>> pytest tests/agents/ingestion/test_authority_domains.py -v
"""

from __future__ import annotations

import re

import pytest

from agents.ingestion.authority_domains import (
    _AUTHORITY_DOMAINS,
    domains_for_category,
)
from agents.pipeline.categories import TOPIC_CATEGORIES

# Bare, lowercase host: labels of [a-z0-9-] joined by dots, a 2+ alpha TLD. Rejects a
# scheme (``https://``), a path (``/world``), uppercase (``Reuters.com``), or a bare
# word with no dot. This is the exact shape GDELT's domain / SourceCommonName emits.
_HOST_RE = re.compile(r"^[a-z0-9.-]+\.[a-z]{2,}$")

_MIN_DOMAINS = 8
_MAX_DOMAINS = 20


class TestAuthorityDomainContract:
    """The data contract every fetch cell depends on."""

    def test_every_topic_category_is_present(self) -> None:
        """All 8 TOPIC_CATEGORIES are covered — a dropped category fetches nothing
        for that screen bucket (fail loud, not a silent gap)."""
        assert set(_AUTHORITY_DOMAINS) == set(TOPIC_CATEGORIES)

    @pytest.mark.parametrize("category", TOPIC_CATEGORIES)
    def test_list_size_in_band(self, category: str) -> None:
        """Each category carries 8–20 domains (the ~10–15 target with slack): too few
        starves the cell, too many dilutes authority + bloats the OR-query."""
        n = len(_AUTHORITY_DOMAINS[category])
        assert _MIN_DOMAINS <= n <= _MAX_DOMAINS, f"{category} has {n} domains"

    @pytest.mark.parametrize("category", TOPIC_CATEGORIES)
    def test_every_domain_is_bare_lowercase_host(self, category: str) -> None:
        """Every domain is lowercase + bare-host — a ``https://Reuters.com/world``
        style entry would never match GDELT's ``domain`` and silently fetch zero."""
        for domain in _AUTHORITY_DOMAINS[category]:
            assert _HOST_RE.match(domain), f"{category}: bad domain {domain!r}"

    @pytest.mark.parametrize("category", TOPIC_CATEGORIES)
    def test_no_duplicate_within_category(self, category: str) -> None:
        """No domain is listed twice in a category — a dup wastes an OR atom and
        skews any order-based weighting."""
        domains = _AUTHORITY_DOMAINS[category]
        assert len(domains) == len(set(domains)), f"{category} has duplicates"


class TestAccessor:
    """domains_for_category — pure accessor, fail-loud on unknown."""

    def test_returns_list_for_known_category(self) -> None:
        """A known category returns its non-empty domain list."""
        result = domains_for_category("business")
        assert "reuters.com" in result
        assert result == _AUTHORITY_DOMAINS["business"]

    def test_returns_a_copy_not_the_module_list(self) -> None:
        """The accessor returns a copy so a caller mutating it cannot corrupt the
        shared module data for every later fetch."""
        result = domains_for_category("business")
        result.append("evil.com")
        assert "evil.com" not in _AUTHORITY_DOMAINS["business"]

    def test_unknown_category_raises_not_empty(self) -> None:
        """An unknown category RAISES (never returns []) — a silent [] would emit an
        un-scoped / empty fetch, the exact M4 failure the set exists to prevent."""
        with pytest.raises(KeyError):
            domains_for_category("nonexistent")
