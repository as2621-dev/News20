"""Offline tests for the v2 interest_search_query backfill (issue #28).

Rule 9 — every test encodes WHY the behavior matters:
  - The query is DETERMINISTIC and traceable to the label. WHY: a non-deterministic
    or invented query would drift on re-run and break the "same input → same output"
    contract the whole backfill leans on (no LLM, Rule 5).
  - Every generated query yields >=1 term for BOTH ingestion paths. WHY: a query
    that ``split_anchor_terms`` / ``match_terms`` empties is exactly the skip that
    left these nodes unfollowable — writing another one would be pointless.
  - The UPDATE is SCOPED + IDEMPOTENT (only NULL-query v2 rows; re-run = 0). WHY:
    clobbering a converged node's existing query, or touching a non-v2 interest,
    corrupts the taxonomy; a non-idempotent backfill double-writes on every run.

All I/O is faked at its boundary (an in-memory FakeConn for the UPDATE chain) — NO
network, NO prod. The live double-run is proven separately against prod as the #28
acceptance step; this suite proves the chain's logic.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents.ingestion.adapters.gdelt_bigquery import match_terms
from agents.ingestion.anchor_scalpel import split_anchor_terms
from scripts.seed_catalog import backfill_interest_query as bf
from scripts.seed_catalog import seed_v2


# ── pure builder: happy path ─────────────────────────────────────────────────
def test_build_query_splits_spaced_ampersand_into_phrases() -> None:
    """WHY: comma phrases are what split_anchor_terms recovers → one DOC anchor each."""
    assert bf.build_search_query("Frontier labs & LLMs", "AI") == "Frontier labs, LLMs"


def test_build_query_is_deterministic() -> None:
    """WHY: same input → same output is the no-LLM contract; a re-run must not drift."""
    first = bf.build_search_query("Clean energy & grid", "Environment")
    second = bf.build_search_query("Clean energy & grid", "Environment")
    assert first == second == "Clean energy, grid"


# ── pure builder: edge cases ─────────────────────────────────────────────────
def test_build_query_preserves_spaceless_ampersand() -> None:
    """WHY: 'M&A' must not shatter into 'M'/'A' — only a SPACED ' & ' is a boundary."""
    query = bf.build_search_query("Deals, M&A & private equity", "Business")
    assert query == "Deals, M&A, private equity"


def test_build_query_splits_en_dash() -> None:
    """WHY: 'Russia–Ukraine' is a pairing; two anchors match more articles than one."""
    assert bf.build_search_query("Russia–Ukraine", "Geopolitics") == "Russia, Ukraine"


def test_build_query_hyphen_becomes_space_not_boundary() -> None:
    """WHY: 'Foreign-policy' is one concept — an intra-word hyphen must not split it."""
    assert (
        bf.build_search_query("Foreign-policy analysts", "Geopolitics")
        == "Foreign policy analysts"
    )


def test_generic_label_qualified_with_root() -> None:
    """WHY: a label of only filler/stopwords is indistinguishable across roots."""
    assert bf.is_generic_label("News & analysis") is True
    # Root prepended because the label carries no distinctive anchor of its own.
    assert bf.build_search_query("News & analysis", "AI") == "AI, News, analysis"


def test_generic_label_not_double_qualified_when_root_present() -> None:
    """WHY: 'AI news & analysis' already carries 'AI' — avoid a redundant 'AI, AI …'."""
    query = bf.build_search_query("AI news & analysis", "AI")
    assert query == "AI news, analysis"
    assert not query.lower().startswith("ai, ai")


def test_distinctive_label_is_not_generic() -> None:
    """WHY: a topical label must keep its own words, not be diluted by the root."""
    assert bf.is_generic_label("Frontier labs & LLMs") is False


# ── whole-catalog integration (no mock): the real 120 ────────────────────────
def test_plan_covers_all_120_with_usable_queries() -> None:
    """WHY: every node must become followable — non-empty query, >=1 term BOTH paths.

    This is the near-integration check: the real catalog through the real builder
    and the real ingestion tokenizers, asserting nothing an actual run would skip.
    """
    plan = bf.build_backfill_plan(seed_v2.load_catalog())
    assert len(plan.rows) == 120
    for row in plan.rows:
        query = row["interest_search_query"]
        assert query, f"empty query for {row['interest_slug']}"
        assert split_anchor_terms(query), f"no DOC anchor for {row['interest_slug']}"
        assert match_terms(query), f"no BigQuery term for {row['interest_slug']}"
    # Slugs are unique (no intra-batch collision that would mis-target the UPDATE).
    assert len({row["interest_slug"] for row in plan.rows}) == 120


# ── scoped + idempotent UPDATE (FakeConn boundary) ───────────────────────────
class FakeConn:
    """In-memory stand-in for asyncpg enforcing the scoped-UPDATE semantics.

    Holds the interests table as slug → query (None = NULL). The UPDATE only
    changes a row when its slug matches AND its query is currently None, exactly
    like the ``interest_search_query is null`` predicate the runner uses.
    """

    def __init__(self, table: dict[str, str | None]) -> None:
        self.table = dict(table)

    def transaction(self) -> "FakeConn":
        return self

    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        # fetch_null_query_slugs: v2 slugs whose query is NULL.
        wanted = set(args[0])
        return [
            {"interest_slug": slug}
            for slug, value in self.table.items()
            if slug in wanted and value is None
        ]

    async def fetchval(self, query: str, *args: Any) -> Any:
        # count_non_v2_with_query: rows with a query whose slug is NOT in the v2 set.
        wanted = set(args[0])
        return sum(
            1
            for slug, value in self.table.items()
            if value is not None and slug not in wanted
        )

    async def execute(self, query: str, *args: Any) -> str:
        slug, new_query = args
        if self.table.get(slug) is None and slug in self.table:
            self.table[slug] = new_query
            return "UPDATE 1"
        return "UPDATE 0"


def _plan(rows: list[dict[str, str]]) -> bf.BackfillPlan:
    return bf.rows_to_plan(rows)


def test_apply_backfill_scoped_to_null_then_idempotent() -> None:
    """WHY: fills only NULL v2 rows, never clobbers an existing query, re-run = 0."""
    rows = [
        {
            "interest_slug": "ai.frontier-labs-and-llms",
            "interest_label": "x",
            "interest_search_query": "Frontier labs, LLMs",
        },
        {
            "interest_slug": "sport.cricket",
            "interest_label": "y",
            "interest_search_query": "Cricket",
        },
    ]
    plan = _plan(rows)
    # sport.cricket already has a query (a converged pre-existing slug); a non-v2 row
    # also carries one and must be left untouched.
    conn = FakeConn(
        {
            "ai.frontier-labs-and-llms": None,
            "sport.cricket": "India cricket team BCCI",
            "geopolitics.middle-east": "some other interest with a query",
        }
    )

    before = asyncio.run(bf.count_non_v2_with_query(conn, plan))
    changed = asyncio.run(bf.apply_backfill(conn, plan))
    after = asyncio.run(bf.count_non_v2_with_query(conn, plan))

    assert changed == 1  # only the NULL v2 row
    assert conn.table["ai.frontier-labs-and-llms"] == "Frontier labs, LLMs"
    assert conn.table["sport.cricket"] == "India cricket team BCCI"  # NOT clobbered
    assert before == after == 1  # non-v2 row unchanged

    # Second run is a no-op — every v2 row now has a query.
    changed_again = asyncio.run(bf.apply_backfill(conn, plan))
    assert changed_again == 0


def test_fetch_null_query_slugs_excludes_filled() -> None:
    """WHY: the write set is precisely the NULL-query v2 rows — nothing already filled."""
    rows = [
        {"interest_slug": "ai.a", "interest_label": "a", "interest_search_query": "A"},
        {"interest_slug": "ai.b", "interest_label": "b", "interest_search_query": "B"},
    ]
    plan = _plan(rows)
    conn = FakeConn({"ai.a": None, "ai.b": "already here"})
    null_slugs = asyncio.run(bf.fetch_null_query_slugs(conn, plan))
    assert null_slugs == {"ai.a"}


def test_duplicate_slug_in_plan_fails_loud() -> None:
    """WHY: two rows with one slug would mis-target the UPDATE — fail, don't write."""
    rows = [
        {
            "interest_slug": "ai.dup",
            "interest_label": "a",
            "interest_search_query": "A",
        },
        {
            "interest_slug": "ai.dup",
            "interest_label": "b",
            "interest_search_query": "B",
        },
    ]
    with pytest.raises(ValueError, match="duplicate interest_slug"):
        bf.rows_to_plan(rows)
