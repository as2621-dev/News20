"""Deterministic backfill of ``interest_search_query`` for the 120 v2 sub-niche nodes (issue #28).

Why this exists
---------------
The greenfield v2 seeder (``seed_v2.py``, issue #15) minted 120 depth-1 sub-niche
interest nodes carrying slug/label/depth/parent/sort only — ``interest_search_query``
was left NULL. But ingestion SKIPS interest picks with no usable query terms
(``gdelt_bigquery.py`` ``match_terms`` → empty ⇒ skipped; ``anchor_scalpel.py``
``split_anchor_terms`` → empty ⇒ no anchor). So those sub-niches are seeded-but-
**unfollowable** until a query is back-filled. This module closes that gap.

How the query is derived (deterministic — NO LLM, Rule 5)
--------------------------------------------------------
For each sub-niche label (e.g. ``"Frontier labs & LLMs"``) under a root
(e.g. ``"AI"``):

1. Normalize the label into comma-separated **anchor phrases**: a spaced
   ampersand ``" & "``, a slash, or an en-/em-dash becomes a phrase boundary
   (``,``); an intra-word hyphen becomes a space; whitespace is collapsed. A
   spaceless ``&`` (e.g. ``M&A``) is preserved, never shattered. The comma
   separators are exactly what ``split_anchor_terms`` splits back on, so the DOC
   scalpel fires one exact-phrase query per phrase; the same commas tokenize
   cleanly for the BigQuery ``match_terms`` batch path.
2. If the label is **generic** — i.e. every ≥3-char token is a stopword /
   content-free event word / descriptor filler, so it carries no distinctive
   anchor of its own — qualify it with the **root label** (prepended as the
   first phrase), UNLESS the root term is already present in the label.

The result is fully traceable to the catalog (same input → same output; never
invented) and is guaranteed to yield ≥1 usable term for BOTH ingestion paths.

Safety (prod write)
-------------------
Dry-run is the DEFAULT: compute + print every ``(slug → query)`` pair and the
row-change count, write nothing. ``--live`` is required to write. The UPDATE is
**scoped**: only rows whose slug is in the v2 set AND whose ``interest_search_query
IS NULL`` — a node that already has a query is never clobbered, so a re-run is a
no-op (idempotent). Parameterized SQL only; the connection string is never logged.

Usage
-----
    # dry-run (compute + report, no writes) — ALWAYS run this first:
    .venv/bin/python -m scripts.seed_catalog.backfill_interest_query

    # live backfill (writes to prod via the IPv4 session pooler):
    .venv/bin/python -m scripts.seed_catalog.backfill_interest_query --live

Env (from .env, never logged): ``SUPABASE_DB_URL`` (percent-encoded session pooler).
"""

from __future__ import annotations

import argparse
import asyncio
import re
from typing import Any

from agents.ingestion.adapters.gdelt_bigquery import (
    _GENERIC_BROAD,
    _QUERY_STOPWORDS,
    match_terms,
)
from agents.ingestion.anchor_scalpel import split_anchor_terms
from agents.shared.logger import get_logger
from scripts.seed_catalog.seed_v2 import V2Catalog, load_catalog, subniche_interest_slug
from scripts.seed_catalog.seed_via_pooler import _connect

logger = get_logger("seed_catalog.backfill_interest_query")

# Reason: descriptor words that name the FORMAT of a sub-niche ("desks", "analysts",
# "media") rather than its TOPIC. They repeat across roots, so a label built only
# from them (+ stopwords/event words) carries no distinctive anchor and must be
# qualified by its root. Used ONLY to classify a label as generic — never stripped
# from the emitted query (the query keeps the verbatim words for matching).
_DESCRIPTOR_FILLER: frozenset[str] = frozenset(
    {
        "analysis",
        "analysts",
        "desk",
        "desks",
        "media",
        "thinkers",
        "scholars",
        "columnists",
        "explainers",
        "insiders",
        "coverage",
        "briefing",
        "wonks",
        "voices",
        "essayists",
        "critics",
        "commentary",
    }
)

# Reason: phrase boundaries inside a label — a SPACED ampersand, a slash, or an
# en-/em-dash each separates two distinct anchors. The ampersand is matched only
# with surrounding whitespace so a spaceless entity ``&`` (e.g. ``M&A``) survives
# as one token instead of being split into meaningless ``M`` / ``A``.
_PHRASE_BOUNDARY = re.compile(r"\s+&\s+|\s*/\s*|\s*[–—]\s*")
_WHITESPACE_RUN = re.compile(r"\s+")


def _label_tokens(label: str) -> list[str]:
    """Lower-cased alphanumeric tokens of length >= 3 (mirrors the ingestion tokenizer)."""
    return [t for t in re.findall(r"[a-z0-9]+", label.lower()) if len(t) >= 3]


def is_generic_label(subniche_label: str) -> bool:
    """True when a label carries no distinctive anchor of its own.

    A label is generic when every one of its >=3-char tokens is a stopword, a
    content-free event word, or a descriptor-format word — i.e. nothing topical
    survives. Such a label is indistinguishable across roots (e.g. ``"News &
    analysis"``) and must be qualified by its root label.

    Args:
        subniche_label: The verbatim sub-niche display string from the catalog.

    Returns:
        Whether the label needs root qualification.

    Example:
        >>> is_generic_label("News & analysis")
        True
        >>> is_generic_label("Frontier labs & LLMs")
        False
    """
    tokens = _label_tokens(subniche_label)
    if not tokens:
        return True
    non_distinctive = _QUERY_STOPWORDS | _GENERIC_BROAD | _DESCRIPTOR_FILLER
    return all(token in non_distinctive for token in tokens)


def _split_phrases(subniche_label: str) -> list[str]:
    """Normalize a label into ordered, de-duplicated, non-empty anchor phrases."""
    phrases: list[str] = []
    seen: set[str] = set()
    for chunk in _PHRASE_BOUNDARY.split(subniche_label):
        # Intra-word hyphen → space ("Foreign-policy" → "Foreign policy"); collapse ws.
        phrase = _WHITESPACE_RUN.sub(" ", chunk.replace("-", " ")).strip()
        # Drop empties and bare connective tokens (a stray "and" / "&").
        if not phrase or phrase.lower() in {"and", "&"}:
            continue
        if phrase.lower() not in seen:
            seen.add(phrase.lower())
            phrases.append(phrase)
    return phrases


def build_search_query(subniche_label: str, root_label: str) -> str:
    """Build the deterministic ``interest_search_query`` for a v2 sub-niche node.

    The label is split into comma-separated anchor phrases (see module docstring);
    a generic label is qualified with its root label unless the root term is
    already present. The comma separators are what ``split_anchor_terms`` recovers
    (DOC scalpel) and what ``match_terms`` tokenizes over (BigQuery batch), so the
    result is a usable query for both ingestion paths.

    Args:
        subniche_label: The verbatim sub-niche label (e.g. ``"AI news & analysis"``).
        root_label: The parent root's display label (e.g. ``"AI"``).

    Returns:
        The comma-joined query string (deterministic; same input → same output).

    Example:
        >>> build_search_query("Frontier labs & LLMs", "AI")
        'Frontier labs, LLMs'
        >>> build_search_query("Russia–Ukraine", "Geopolitics")
        'Russia, Ukraine'
    """
    phrases = _split_phrases(subniche_label)
    if is_generic_label(subniche_label):
        root_phrase = _WHITESPACE_RUN.sub(" ", root_label.strip())
        joined_lower = " ".join(phrases).lower()
        # Only prepend the root when it is not already carried by the label — avoids a
        # redundant "AI, AI news, analysis" while still rescuing a bare "News & analysis".
        if root_phrase and root_phrase.lower() not in joined_lower:
            phrases.insert(0, root_phrase)
    return ", ".join(phrases)


class BackfillPlan:
    """The computed (slug, label, query) triples for the 120 v2 sub-niche nodes.

    Kept as a plain object (not a dataclass) to mirror the seeder's flat style; the
    query builder is the load-bearing logic and lives in ``build_search_query``.
    """

    def __init__(self, rows: list[dict[str, str]]) -> None:
        self.rows = (
            rows  # each: {"interest_slug", "interest_label", "interest_search_query"}
        )


def build_backfill_plan(catalog: V2Catalog) -> BackfillPlan:
    """Compute the full (slug → query) plan for every v2 sub-niche node.

    Every generated query is asserted to yield >=1 usable term for BOTH ingestion
    paths (``split_anchor_terms`` and ``match_terms``) — a query that ingestion
    would still skip is a fail-loud bug, not something to write to prod.

    Args:
        catalog: The loaded v2 catalog.

    Returns:
        The backfill plan (one row per sub-niche node).

    Raises:
        ValueError: If any generated query would still be skipped by ingestion.
    """
    rows: list[dict[str, str]] = []
    for root in catalog.roots:
        for subniche in root.subniches:
            query = build_search_query(subniche, root.root_label)
            if not split_anchor_terms(query):
                raise ValueError(
                    f"query {query!r} for {subniche!r} yields no DOC anchor terms. "
                    "fix_suggestion: the label produced an empty query — investigate _split_phrases."
                )
            if not match_terms(query):
                raise ValueError(
                    f"query {query!r} for {subniche!r} yields no BigQuery match terms. "
                    "fix_suggestion: the label is all stopwords — qualify it with the root label."
                )
            rows.append(
                {
                    "interest_slug": subniche_interest_slug(root.root_slug, subniche),
                    "interest_label": subniche,
                    "interest_search_query": query,
                }
            )
    return rows_to_plan(rows)


def rows_to_plan(rows: list[dict[str, str]]) -> BackfillPlan:
    """Wrap plan rows, failing loud on an intra-batch duplicate slug (would mis-target)."""
    slugs = [row["interest_slug"] for row in rows]
    duplicates = sorted({slug for slug in slugs if slugs.count(slug) > 1})
    if duplicates:
        raise ValueError(
            f"duplicate interest_slug(s) in plan {duplicates}. "
            "fix_suggestion: two sub-niches slugify identically — disambiguate the catalog labels."
        )
    return BackfillPlan(rows)


# ── prod I/O (scoped, idempotent) ────────────────────────────────────────────
async def fetch_null_query_slugs(conn: Any, plan: BackfillPlan) -> set[str]:
    """The subset of v2 slugs whose ``interest_search_query`` is currently NULL.

    These are the ONLY rows the live UPDATE will touch — a node that already has a
    query (a converged pre-existing slug) is excluded, never clobbered.
    """
    slugs = [row["interest_slug"] for row in plan.rows]
    fetched = await conn.fetch(
        "select interest_slug from interests "
        "where interest_slug = any($1::text[]) and interest_search_query is null",
        slugs,
    )
    return {record["interest_slug"] for record in fetched}


async def count_non_v2_with_query(conn: Any, plan: BackfillPlan) -> int:
    """Count interests OUTSIDE the v2 set that already have a query (scope guard).

    Snapshotting this before + after the write proves the backfill touched ONLY the
    120 v2 nodes — the count must be identical either side.
    """
    slugs = [row["interest_slug"] for row in plan.rows]
    return await conn.fetchval(
        "select count(*) from interests "
        "where interest_search_query is not null and interest_slug != all($1::text[])",
        slugs,
    )


async def apply_backfill(conn: Any, plan: BackfillPlan) -> int:
    """Set ``interest_search_query`` for v2 nodes that are still NULL; return rows changed.

    The ``interest_search_query is null`` predicate makes the UPDATE idempotent and
    scoped: a re-run (every node already filled) changes zero rows, and a node that
    already carried a query is never overwritten. Parameterized per row.

    Args:
        conn: An open asyncpg connection (the session pooler).
        plan: The computed backfill plan.

    Returns:
        The number of rows actually updated (NULL → query).
    """
    query = (
        "update interests set interest_search_query = $2 "
        "where interest_slug = $1 and interest_search_query is null"
    )
    changed = 0
    async with conn.transaction():
        for row in plan.rows:
            status = await conn.execute(
                query, row["interest_slug"], row["interest_search_query"]
            )
            # asyncpg returns "UPDATE <n>"; count only rows that actually changed.
            if status.rsplit(" ", 1)[-1] != "0":
                changed += 1
    return changed


def format_dry_run_report(plan: BackfillPlan, null_slugs: set[str]) -> str:
    """Render the dry-run listing: every (slug → query) pair, flagged NULL vs skip."""
    lines = ["=== interest_search_query backfill — dry run (issue #28) ==="]
    lines.append(
        f"v2 sub-niche nodes: {len(plan.rows)} · would update (NULL query): "
        f"{len(null_slugs)} · skip (already have query): {len(plan.rows) - len(null_slugs)}"
    )
    lines.append("")
    for row in plan.rows:
        flag = "WRITE" if row["interest_slug"] in null_slugs else "skip "
        lines.append(
            f"  [{flag}] {row['interest_slug']:<42} → {row['interest_search_query']}"
        )
    return "\n".join(lines)


async def _run(args: argparse.Namespace) -> None:
    catalog = load_catalog()
    plan = build_backfill_plan(catalog)

    conn = await _connect()
    try:
        null_slugs = await fetch_null_query_slugs(conn, plan)
        print(format_dry_run_report(plan, null_slugs))

        if not args.live:
            logger.info(
                "backfill_dry_run_complete",
                v2_nodes=len(plan.rows),
                would_update=len(null_slugs),
                message="no writes performed; pass --live to backfill",
            )
            return

        non_v2_before = await count_non_v2_with_query(conn, plan)
        changed = await apply_backfill(conn, plan)
        non_v2_after = await count_non_v2_with_query(conn, plan)
        remaining_null = await fetch_null_query_slugs(conn, plan)
        logger.info(
            "backfill_live_complete",
            rows_changed=changed,
            v2_nodes=len(plan.rows),
            non_v2_with_query_before=non_v2_before,
            non_v2_with_query_after=non_v2_after,
            non_v2_unchanged=(non_v2_before == non_v2_after),
            remaining_null_after=len(remaining_null),
        )
    finally:
        await conn.close()


def main() -> None:
    """CLI entry point for the interest_search_query backfill (dry-run by default)."""
    parser = argparse.ArgumentParser(
        description="Backfill interest_search_query for the 120 v2 sub-niche nodes (deterministic)."
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Write to prod (default: dry-run report only).",
    )
    args = parser.parse_args()
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
