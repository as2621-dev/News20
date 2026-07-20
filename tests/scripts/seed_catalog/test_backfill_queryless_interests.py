"""Offline tests for the queryless-interest sweep backfill (issue #36).

Rule 9 — every test encodes WHY the behavior matters:
  - Root queries come from CURATED data and an unmapped root FAILS LOUD. WHY: a
    1-word root label ("AI") cannot deterministically become a usable query — a
    guessed root query would ingest a garbage firehose for every follower.
  - Deeper (user-minted) nodes are root-qualified. WHY: a minted rung's label is a
    single context-free segment ("Business" under ai.*) — unqualified it ingests
    the wrong topic entirely.
  - Every derived query yields >=1 term for BOTH ingestion tokenizers. WHY: a query
    that ``split_anchor_terms``/``match_terms`` empties is exactly the skip this
    backfill exists to close — writing it would be pointless.
  - The UPDATE re-checks the queryless predicate per row. WHY: idempotence (re-run
    = 0) and never clobbering a query that appeared between plan and write.

All I/O is faked at the boundary (an in-memory FakeConn) — NO network, NO prod.
The live dry-run + apply is proven against prod as the #36 acceptance step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agents.ingestion.adapters.gdelt_bigquery import match_terms
from agents.ingestion.anchor_scalpel import split_anchor_terms
from scripts.seed_catalog import backfill_queryless_interests as sweep


# ── derive_query: happy path ─────────────────────────────────────────────────
def test_root_query_comes_from_curated_map() -> None:
    """WHY: 'AI' has zero >=3-char tokens — only curated data can back a root."""
    assert (
        sweep.derive_query("ai", "AI", 0, "AI")
        == "artificial intelligence, machine learning"
    )


def test_minted_rung_is_root_qualified() -> None:
    """WHY: 'Startups' under ai.* is context-free — the qualifier keeps it on-ladder."""
    query = sweep.derive_query("ai.business.startups", "Startups", 2, "AI")
    assert query == "artificial intelligence, Startups"


# ── derive_query: edge cases ─────────────────────────────────────────────────
def test_unmapped_queryless_root_fails_loud() -> None:
    """WHY: a root query is curated, never guessed — fail, don't write garbage."""
    with pytest.raises(ValueError, match="no curated root query"):
        sweep.derive_query("mystery-root", "Mystery", 0, "Mystery")


def test_label_already_carrying_root_term_is_not_double_qualified() -> None:
    """WHY: avoid a redundant 'technology, Tech gadgets' — the term is already there."""
    query = sweep.derive_query(
        "tech.technology-reviews", "Technology reviews", 1, "Tech"
    )
    assert query.lower().count("technology") == 1


def test_qualifier_presence_check_is_word_bounded() -> None:
    """WHY: 'sport' inside 'Transportation' must NOT count as the root term being
    present — a substring false-positive would skip the qualifier and write a
    broad off-ladder query (the exact firehose the qualifier prevents)."""
    query = sweep.derive_query(
        "sport.transportation-costs", "Transportation costs", 1, "Sport"
    )
    assert query == "Sport, Transportation costs"


def test_all_curated_root_queries_pass_both_tokenizers() -> None:
    """WHY: a curated root query that ingestion still skips is a self-defeating map."""
    for root_slug, root_query in sweep.ROOT_QUERIES.items():
        assert split_anchor_terms(root_query), f"no DOC anchor for root {root_slug}"
        assert match_terms(root_query), f"no BigQuery term for root {root_slug}"


# ── build_plan: validation + fail-loud ───────────────────────────────────────
def _row(interest_id: str, slug: str, label: str, depth: int) -> dict[str, Any]:
    return {
        "interest_id": interest_id,
        "interest_slug": slug,
        "interest_label": label,
        "depth_level": depth,
    }


_ROOT_LABELS = {"ai": "AI", "tech": "Tech", "sport": "Sport"}


def test_build_plan_extends_rows_with_valid_queries() -> None:
    """WHY: the plan is the exact write set — every row must carry a usable query."""
    rows = [
        _row("id-1", "ai", "AI", 0),
        _row("id-2", "ai.business", "Business", 1),
        _row("id-3", "tech.semiconductors", "Semiconductors", 1),
    ]
    plan = sweep.build_plan(rows, _ROOT_LABELS)
    assert [r["interest_search_query"] for r in plan] == [
        "artificial intelligence, machine learning",
        "artificial intelligence, Business",
        "technology, Semiconductors",
    ]
    for row in plan:
        assert split_anchor_terms(row["interest_search_query"])
        assert match_terms(row["interest_search_query"])


def test_build_plan_orphan_root_fails_loud() -> None:
    """WHY: a node under a missing root is taxonomy corruption — never backfill over it."""
    with pytest.raises(ValueError, match="no depth-0 root"):
        sweep.build_plan([_row("id-9", "ghost.leaf", "Leaf", 1)], _ROOT_LABELS)


# ── scoped + idempotent UPDATE (FakeConn boundary) ───────────────────────────
class FakeConn:
    """In-memory asyncpg stand-in enforcing the re-checked queryless predicate.

    Holds interests as ``interest_id → query``. Queryless = None or whitespace-only,
    mirroring BOTH the SQL predicate (``~ '^\\s*$'``) and the pipeline's ``.strip()``
    skip check — the two must stay semantically identical (review finding #36).
    """

    def __init__(self, table: dict[str, str | None]) -> None:
        self.table = dict(table)

    def transaction(self) -> "FakeConn":
        return self

    async def __aenter__(self) -> "FakeConn":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        return None

    async def execute(self, query: str, *args: Any) -> str:
        interest_id, new_query = args
        current = self.table.get(interest_id, "missing")
        if current is None or (isinstance(current, str) and current.strip() == ""):
            self.table[interest_id] = new_query
            return "UPDATE 1"
        return "UPDATE 0"


def test_apply_backfill_fills_null_and_empty_then_idempotent() -> None:
    """WHY: NULL and empty-string are BOTH ingestion skips; re-run must change 0 rows
    and a row that already has a query must never be clobbered."""
    plan = [
        {
            **_row("id-null", "ai.a", "A", 1),
            "interest_search_query": "artificial intelligence, A",
        },
        {
            **_row("id-empty", "ai.b", "B", 1),
            "interest_search_query": "artificial intelligence, B",
        },
        {**_row("id-filled", "ai.c", "C", 1), "interest_search_query": "would clobber"},
    ]
    conn = FakeConn({"id-null": None, "id-empty": "  ", "id-filled": "existing query"})

    changed = asyncio.run(sweep.apply_backfill(conn, plan))
    assert changed == 2  # NULL + empty filled; the filled row untouched
    assert conn.table["id-null"] == "artificial intelligence, A"
    assert conn.table["id-empty"] == "artificial intelligence, B"
    assert conn.table["id-filled"] == "existing query"  # NOT clobbered

    changed_again = asyncio.run(sweep.apply_backfill(conn, plan))
    assert changed_again == 0  # idempotent re-run
