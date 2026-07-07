"""Sweep backfill of ``interest_search_query`` for EVERY queryless interest (issue #36).

Why this exists
---------------
Ingestion skips a followed interest that has no usable ``interest_search_query``
(``interest_keyed_pipeline.build_active_interest_set`` → ``queryless_interest_skipped``),
so a queryless interest silently produces nothing for its followers. The v2 catalog
backfill (``backfill_interest_query.py``, issue #28) covered the 120 catalog sub-niche
nodes only — this script is the *sweep*: it reads prod for ALL interests whose query is
NULL **or empty-string** (the mint RPC and legacy seeders can leave either), derives a
deterministic query for each, and fills exactly those rows. Stragglers include:

  * depth-0 roots minted without queries (0023 / legacy taxonomy roots), and
  * user-minted ladder rungs from chat-onboarding drills (``mint_interest_ladder``
    leaves intermediate nodes — and query-less leaves — with a NULL query).

How the query is derived (deterministic — NO LLM, Rule 5)
--------------------------------------------------------
  * **depth-0 root** → a curated comma-phrase query from ``ROOT_QUERIES`` (a root
    label like "AI" is 2 chars — both ingestion tokenizers would empty it, so roots
    need curated data, mirroring the 2026-06-16 ``backfill_interest_queries.py``
    precedent). An unmapped queryless root FAILS LOUD (never a guessed query).
  * **deeper node** → ``backfill_interest_query.build_search_query(label, qualifier)``
    (the issue-#28 label→phrase rule), then the root qualifier is prepended when the
    label does not already carry it. Reason: a minted rung's label is a single
    context-free segment ("Business" under ``ai.``) — unqualified it would ingest the
    wrong firehose; "artificial intelligence, Business" stays on-ladder. The
    qualifier is the root's label, expanded via ``ROOT_QUERY_QUALIFIERS`` for
    abbreviated roots ("AI" → "artificial intelligence") so it survives the >=3-char
    BigQuery tokenizer.

Every derived query is asserted to yield >=1 usable term for BOTH ingestion paths
(``split_anchor_terms`` + ``match_terms``) — a still-skippable query fails loud at
plan time and never reaches prod.

Safety (prod write)
-------------------
Dry-run is the DEFAULT: print every ``(slug → query)`` pair, write nothing. ``--live``
is required to write. The UPDATE is scoped per ``interest_id`` AND re-checks
``interest_search_query is null or btrim(...) = ''`` — a row that gained a query
between plan and write is never clobbered, and a re-run changes zero rows
(idempotent). Parameterized SQL only; the connection string is never logged.

Usage
-----
    # dry-run (report only) — ALWAYS run this first:
    .venv/bin/python -m scripts.seed_catalog.backfill_queryless_interests

    # live backfill (writes to prod via the IPv4 session pooler):
    .venv/bin/python -m scripts.seed_catalog.backfill_queryless_interests --live

Env (from .env, never logged): ``SUPABASE_DB_URL`` (percent-encoded session pooler).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

from agents.ingestion.adapters.gdelt_bigquery import match_terms
from agents.ingestion.anchor_scalpel import split_anchor_terms
from agents.shared.logger import get_logger
from scripts.seed_catalog.backfill_interest_query import build_search_query
from scripts.seed_catalog.seed_via_pooler import _connect

logger = get_logger("seed_catalog.backfill_queryless_interests")

# Reason: a depth-0 root's own label is too short/broad to derive from ("AI" → zero
# >=3-char tokens; "Arts" alone is a firehose). Curated comma-phrase queries — the
# comma is what split_anchor_terms recovers (one DOC exact-phrase anchor each) and
# match_terms tokenizes over. Data, not judgment (Rule 5); style mirrors the
# 2026-06-16 root entries in scripts/backfill_interest_queries.py.
ROOT_QUERIES: dict[str, str] = {
    "ai": "artificial intelligence, machine learning",
    "arts": "arts and culture, books, film, music",
    "business": "business, economy, corporate earnings",
    "environment": "climate change, renewable energy, extreme weather",
    "geopolitics": "geopolitics, diplomacy, foreign policy",
    "politics": "politics, elections, government policy",
    "sport": "sports, championship, tournament",
    "tech": "technology, software, gadgets",
}

# Reason: an abbreviated root label used as a phrase qualifier must survive the
# >=3-char BigQuery tokenizer — "AI" contributes ZERO match terms, so deeper nodes
# under these roots are qualified with the spelled-out form instead.
ROOT_QUERY_QUALIFIERS: dict[str, str] = {
    "ai": "artificial intelligence",
    "tech": "technology",
}


def derive_query(
    interest_slug: str,
    interest_label: str,
    depth_level: int,
    root_label: str,
) -> str:
    """Derive the deterministic ``interest_search_query`` for one queryless interest.

    Args:
        interest_slug: The node's full dotted slug (e.g. ``"ai.business.startups"``).
        interest_label: The node's display label (e.g. ``"Startups"``).
        depth_level: 0 for a root; >=1 for any deeper node.
        root_label: The display label of the node's depth-0 root (e.g. ``"AI"``).

    Returns:
        The comma-phrase query (same input → same output; never invented).

    Raises:
        ValueError: For a queryless depth-0 root with no curated ``ROOT_QUERIES``
            entry — a root query is curated data, never guessed.

    Example:
        >>> derive_query("ai", "AI", 0, "AI")
        'artificial intelligence, machine learning'
        >>> derive_query("ai.business.startups", "Startups", 2, "AI")
        'artificial intelligence, Startups'
    """
    root_slug = interest_slug.split(".", 1)[0]
    if depth_level == 0:
        root_query = ROOT_QUERIES.get(interest_slug)
        if root_query is None:
            raise ValueError(
                f"no curated root query for depth-0 interest {interest_slug!r}. "
                "fix_suggestion: add an entry to ROOT_QUERIES in "
                "scripts/seed_catalog/backfill_queryless_interests.py — root queries "
                "are curated data, never derived from a 1-word root label."
            )
        return root_query

    qualifier = ROOT_QUERY_QUALIFIERS.get(root_slug, root_label).strip()
    query = build_search_query(interest_label, qualifier)
    # Reason: a minted rung's label is a single context-free segment ("Business"
    # under ai.*). build_search_query qualifies only GENERIC labels; the sweep
    # qualifies every deeper node so the query stays on-ladder — unless the label
    # already carries the root term (avoids "technology, Tech gadgets").
    if qualifier and qualifier.lower() not in query.lower():
        query = f"{qualifier}, {query}"
    return query


def build_plan(
    rows: list[dict[str, Any]], root_labels: dict[str, str]
) -> list[dict[str, Any]]:
    """Compute the (interest_id → query) plan for every queryless row, fail-loud validated.

    Args:
        rows: Queryless interest rows (``interest_id``/``interest_slug``/
            ``interest_label``/``depth_level``).
        root_labels: Map of depth-0 slug → label (for the deeper-node qualifier).

    Returns:
        Plan rows: the input rows each extended with ``interest_search_query``.

    Raises:
        ValueError: If a node's root is missing from ``root_labels`` (orphan slug),
            or a derived query would still be skipped by either ingestion tokenizer.
    """
    plan: list[dict[str, Any]] = []
    for row in rows:
        root_slug = row["interest_slug"].split(".", 1)[0]
        root_label = root_labels.get(root_slug)
        if root_label is None:
            raise ValueError(
                f"interest {row['interest_slug']!r} has no depth-0 root {root_slug!r}. "
                "fix_suggestion: the taxonomy is missing this root node — investigate "
                "before backfilling (migration 0023 should have minted all roots)."
            )
        query = derive_query(
            row["interest_slug"], row["interest_label"], row["depth_level"], root_label
        )
        if not split_anchor_terms(query) or not match_terms(query):
            raise ValueError(
                f"derived query {query!r} for {row['interest_slug']!r} would still be "
                "skipped by ingestion. fix_suggestion: the label is all stopwords — "
                "extend ROOT_QUERY_QUALIFIERS or curate this row by hand."
            )
        plan.append({**row, "interest_search_query": query})
    return plan


# ── prod I/O (scoped, idempotent) ────────────────────────────────────────────
_QUERYLESS_PREDICATE = (
    "(interest_search_query is null or btrim(interest_search_query) = '')"
)


async def fetch_queryless_interests(conn: Any) -> list[dict[str, Any]]:
    """Every interest whose query is NULL or empty-string (both are skipped by ingestion)."""
    fetched = await conn.fetch(
        "select interest_id, interest_slug, interest_label, depth_level "
        f"from interests where {_QUERYLESS_PREDICATE} order by interest_slug"
    )
    return [dict(record) for record in fetched]


async def fetch_root_labels(conn: Any) -> dict[str, str]:
    """Map of depth-0 slug → label (the deeper-node qualifier source)."""
    fetched = await conn.fetch(
        "select interest_slug, interest_label from interests where depth_level = 0"
    )
    return {record["interest_slug"]: record["interest_label"] for record in fetched}


async def count_rows_with_query(conn: Any) -> int:
    """Rows that already carry a query — snapshot before/after proves no clobber.

    Every planned row is queryless, so the with-query count may only GROW by exactly
    the number of rows changed; any pre-existing query changing would break that
    arithmetic (checked by the runner).
    """
    return await conn.fetchval(
        f"select count(*) from interests where not {_QUERYLESS_PREDICATE}"
    )


async def apply_backfill(conn: Any, plan: list[dict[str, Any]]) -> int:
    """Fill each planned row's query iff it is STILL queryless; return rows changed.

    The re-checked queryless predicate makes the UPDATE idempotent (re-run = 0 rows)
    and non-clobbering (a row that gained a query since the plan was computed is
    skipped). Parameterized per row, one transaction.
    """
    changed = 0
    async with conn.transaction():
        for row in plan:
            status = await conn.execute(
                "update interests set interest_search_query = $2 "
                f"where interest_id = $1 and {_QUERYLESS_PREDICATE}",
                row["interest_id"],
                row["interest_search_query"],
            )
            # asyncpg returns "UPDATE <n>"; count only rows that actually changed.
            if status.rsplit(" ", 1)[-1] != "0":
                changed += 1
    return changed


def format_dry_run_report(plan: list[dict[str, Any]]) -> str:
    """Render the dry-run listing: every queryless (slug → derived query) pair."""
    lines = ["=== queryless-interest sweep backfill — dry run (issue #36) ==="]
    lines.append(f"queryless interests found: {len(plan)}")
    for row in plan:
        lines.append(
            f"  [WRITE] depth={row['depth_level']} "
            f"{row['interest_slug']:<45} → {row['interest_search_query']}"
        )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> None:
    conn = await _connect()
    try:
        rows = await fetch_queryless_interests(conn)
        if not rows:
            logger.info(
                "backfill_sweep_nothing_to_do",
                queryless_interests=0,
                message="every interest already carries a search query",
            )
            print("No queryless interests — nothing to backfill.")
            return
        root_labels = await fetch_root_labels(conn)
        plan = build_plan(rows, root_labels)
        print(format_dry_run_report(plan))

        if not args.live:
            logger.info(
                "backfill_sweep_dry_run_complete",
                queryless_interests=len(plan),
                message="no writes performed; pass --live to backfill",
            )
            return

        with_query_before = await count_rows_with_query(conn)
        changed = await apply_backfill(conn, plan)
        with_query_after = await count_rows_with_query(conn)
        remaining = await fetch_queryless_interests(conn)
        logger.info(
            "backfill_sweep_live_complete",
            rows_changed=changed,
            planned=len(plan),
            with_query_before=with_query_before,
            with_query_after=with_query_after,
            growth_matches_changed=(with_query_after - with_query_before == changed),
            remaining_queryless=len(remaining),
        )
    finally:
        await conn.close()


def main() -> None:
    """CLI entry point for the queryless-interest sweep backfill (dry-run by default)."""
    # Reason: _connect reads SUPABASE_DB_URL from the environment; load .env the same
    # way scripts/backfill_interest_queries.py does so the module runs standalone.
    from dotenv import load_dotenv

    repo_root = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    load_dotenv(os.path.join(repo_root, ".env"))

    parser = argparse.ArgumentParser(
        description="Sweep-backfill interest_search_query for every queryless interest (deterministic)."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write to prod (default: dry-run report only).",
    )
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
