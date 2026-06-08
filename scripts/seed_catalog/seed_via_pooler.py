"""Seed the content-source catalog via the IPv4 **session pooler** (asyncpg).

Why this exists
---------------
``seed_catalog.py``'s live path upserts through the **supabase-py REST client**,
which talks to ``https://<ref>.supabase.co`` (PostgREST). On IPv4-only networks
that REST host does not resolve (the project's ``<ref>.supabase.co`` has no
reachable A record without IPv6), so every upsert fails with
``[Errno 8] nodename nor servname provided``. The **session pooler**
(``aws-1-us-east-1.pooler.supabase.com:5432``) *is* IPv4-reachable — the same
path the project uses for migrations (see memory ``news20-supabase-ddl-connection``).

This driver reuses **all** of ``seed_catalog.run_seed``'s resolve + merge logic
unchanged and only swaps the persistence boundary: a tiny shim implementing the
``client.table(name).upsert(rows, on_conflict=...).execute()`` surface that the
seeder calls, backed by an ``asyncpg`` ``INSERT … ON CONFLICT DO UPDATE`` against
the pooler. Per-cell flush gives incremental persistence + progress logging.

Usage
-----
    # validate the write path in seconds (writes + deletes a synthetic row):
    .venv/bin/python -m scripts.seed_catalog.seed_via_pooler --selftest

    # seed one cell:
    .venv/bin/python -m scripts.seed_catalog.seed_via_pooler --type podcasts --archetype markets-macro

    # seed one type across all 12 archetypes:
    .venv/bin/python -m scripts.seed_catalog.seed_via_pooler --type podcasts --all-archetypes

Env (from .env, never logged): ``SUPABASE_DB_URL`` (percent-encoded session
pooler), ``YOUTUBE_API_KEY`` (only needed for ``--type channels``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from decimal import Decimal
from typing import Any

import asyncpg
import httpx

from agents.shared.logger import get_logger
from scripts.seed_catalog import itunes_resolve
from scripts.seed_catalog.seed_catalog import (
    ALLOWED_ARCHETYPES,
    WIKIPEDIA_USER_AGENT,
    run_seed,
)

logger = get_logger("seed_catalog.seed_via_pooler")

# The conflict target per table (mirrors seed_catalog's REST upserts).
_CONFLICT_TARGETS: dict[str, tuple[str, ...]] = {
    "content_sources": ("content_source_type", "external_id"),
    "personalities": ("display_name",),
}

# Text[] columns that must ACCUMULATE across archetype cells rather than be
# overwritten. The seeder loops one archetype per ``run_seed``/upsert (the
# ``--all-archetypes`` driver), so a source curated under several archetypes is
# upserted once per archetype; a plain ``= excluded.personas`` would let the
# last-seeded cell clobber the earlier tags (e.g. a `balanced-generalist` person
# also tagged `tech-generalist` would lose whichever was seeded first). Unioning
# de-duplicated keeps every archetype tag — the same persona-union the in-memory
# merge does WITHIN a single cell, extended ACROSS cells at the DB boundary.
_UNION_ARRAY_COLUMNS: frozenset[str] = frozenset({"personas", "topic_tags"})


def _set_expr(table_name: str, column: str) -> str:
    """Build the ``do update set`` expression for one column.

    Array-union columns (``personas`` / ``topic_tags``) accumulate the existing +
    incoming values de-duplicated; every other column is a plain overwrite.

    Args:
        table_name: The target table (qualifies the existing-row reference).
        column: The column being updated.

    Returns:
        The SQL ``<col> = <expr>`` fragment for the ``do update set`` list.
    """
    if column in _UNION_ARRAY_COLUMNS:
        # array(select distinct unnest(existing || incoming)) — order is not
        # significant for these tag arrays (membership is what the deck filters on).
        return (
            f"{column} = array(select distinct unnest("
            f"coalesce({table_name}.{column}, '{{}}') || coalesce(excluded.{column}, '{{}}')))"
        )
    return f"{column} = excluded.{column}"


def _coerce(value: Any) -> Any:
    """Coerce a Python value to what asyncpg expects for our column types.

    ``numeric`` columns (popularity_score) need ``Decimal`` (asyncpg rejects
    float); ``jsonb`` is handled by a connection codec, arrays/enums/None pass
    through natively.

    Args:
        value: The raw cell value from a seeder-built row.

    Returns:
        The asyncpg-safe value.
    """
    if isinstance(value, float):
        return Decimal(str(value))
    return value


class _UpsertBuffer:
    """Collects one table's pending upsert batch (the ``.upsert().execute()`` tail)."""

    def __init__(self, table_name: str, sink: list[tuple[str, list[dict[str, Any]]]]) -> None:
        self.table_name = table_name
        self._sink = sink
        self._rows: list[dict[str, Any]] = []

    def upsert(self, rows: list[dict[str, Any]], *, on_conflict: str | None = None) -> "_UpsertBuffer":
        """Stage rows for upsert (on_conflict is fixed per table, so it is ignored)."""
        self._rows = rows
        return self

    def execute(self) -> None:
        """Hand the staged batch to the driver's flush sink (no DB call here)."""
        if self._rows:
            self._sink.append((self.table_name, self._rows))


class _PoolerClient:
    """Minimal stand-in for the supabase-py client used by ``_upsert_*`` helpers.

    Only ``.table(name).upsert(rows, on_conflict=...).execute()`` is exercised by
    the seeder; everything staged is flushed to the pooler by the driver after
    each cell so a mid-run failure never loses a completed cell.
    """

    def __init__(self) -> None:
        self.pending: list[tuple[str, list[dict[str, Any]]]] = []

    def table(self, table_name: str) -> _UpsertBuffer:
        return _UpsertBuffer(table_name, self.pending)


async def _flush(conn: asyncpg.Connection, pending: list[tuple[str, list[dict[str, Any]]]]) -> dict[str, int]:
    """Apply staged upserts to the pooler; returns per-table submitted counts."""
    counts: dict[str, int] = {}
    for table_name, rows in pending:
        if not rows:
            continue
        conflict = _CONFLICT_TARGETS.get(table_name)
        if conflict is None:
            raise RuntimeError(
                f"no conflict target for table {table_name!r}. "
                "fix_suggestion: add it to _CONFLICT_TARGETS."
            )
        cols = sorted({c for row in rows for c in row})
        if not all(c.replace("_", "").isalpha() for c in cols):
            raise RuntimeError(f"unexpected column name in {cols} — refusing to build SQL")
        placeholders = ", ".join(f"${i + 1}" for i in range(len(cols)))
        update_cols = [c for c in cols if c not in conflict]
        set_clause = (
            ", ".join(_set_expr(table_name, c) for c in update_cols)
            or f"{conflict[0]} = excluded.{conflict[0]}"
        )
        query = (
            f"insert into {table_name} ({', '.join(cols)}) values ({placeholders}) "
            f"on conflict ({', '.join(conflict)}) do update set {set_clause}"
        )
        args = [tuple(_coerce(row.get(c)) for c in cols) for row in rows]
        await conn.executemany(query, args)
        counts[table_name] = counts.get(table_name, 0) + len(rows)
    pending.clear()
    return counts


async def _connect() -> asyncpg.Connection:
    """Open a pooler connection with a jsonb codec (dict ↔ jsonb)."""
    url = os.environ.get("SUPABASE_DB_URL", "").strip()
    if not url:
        raise RuntimeError(
            "SUPABASE_DB_URL is required. "
            "fix_suggestion: export the percent-encoded session-pooler URL from .env."
        )
    conn = await asyncpg.connect(url, statement_cache_size=0)
    await conn.set_type_codec("jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog")
    return conn


async def _selftest(conn: asyncpg.Connection) -> None:
    """Write + read-back + delete a synthetic row to validate the write path."""
    client = _PoolerClient()
    client.table("content_sources").upsert(
        [
            {
                "content_source_type": "youtube_channel",
                "external_id": "__selftest_pooler__",
                "source_name": "SELFTEST",
                "source_description": None,
                "thumbnail_url": None,
                "subscriber_count": 123,
                "platform_metadata": {"probe": True},
                "personas": ["balanced-generalist"],
                "topic_tags": ["tech"],
                "popularity_score": 42.0,
                "is_curated": True,
            }
        ],
        on_conflict="content_source_type,external_id",
    ).execute()
    await _flush(conn, client.pending)
    got = await conn.fetchrow(
        "select source_name, subscriber_count, platform_metadata, personas, popularity_score "
        "from content_sources where external_id='__selftest_pooler__'"
    )
    await conn.execute("delete from content_sources where external_id='__selftest_pooler__'")
    if not got or got["source_name"] != "SELFTEST" or got["platform_metadata"] != {"probe": True}:
        raise RuntimeError(f"selftest mismatch: {got!r}")
    logger.info(
        "pooler_selftest_passed",
        roundtrip=dict(got),
        message="write path (enum + jsonb + array + numeric) OK; synthetic row deleted",
    )


async def _run(args: argparse.Namespace) -> None:
    conn = await _connect()
    try:
        if args.selftest:
            await _selftest(conn)
            return

        youtube_api_key = (os.environ.get("YOUTUBE_API_KEY") or "").strip()
        if args.type in (None, "channels") and not youtube_api_key:
            raise RuntimeError(
                "YOUTUBE_API_KEY required for channels. "
                "fix_suggestion: export it, or pass --type podcasts/x/personalities."
            )
        # Pace iTunes under its per-IP rate limit for podcast seeds (mirrors the CLI).
        if args.type in (None, "podcasts"):
            itunes_resolve.set_pace_interval(itunes_resolve.BULK_REQUEST_INTERVAL_SECONDS)

        # --all-archetypes runs ONE merged pass (archetype_filter=None): load_entries
        # loads every archetype file and the in-memory merge UNIONS personas, so each
        # unique source is resolved once and upserted once with its full persona set.
        # This is clobber-proof by construction — unlike a per-archetype loop, where a
        # cross-tagged source is re-upserted per cell and a plain replace strips the
        # other archetypes' tags (the bug that dropped startup-operator channels to 45).
        if args.all_archetypes:
            archetypes: list[str | None] = [None]
        else:
            archetypes = [args.archetype]

        async with httpx.AsyncClient(headers={"User-Agent": WIKIPEDIA_USER_AGENT}) as http_client:
            for archetype in archetypes:
                client = _PoolerClient()
                summary = await run_seed(
                    supabase_client=client,
                    http_client=http_client,
                    youtube_api_key=youtube_api_key,
                    type_filter=args.type,
                    archetype_filter=archetype,
                )
                counts = await _flush(conn, client.pending)
                logger.info(
                    "pooler_cell_seeded",
                    archetype=archetype,
                    type=args.type,
                    upserted=counts,
                    channels_unresolved=summary.channels_unresolved,
                    podcasts_unresolved=summary.podcasts_unresolved,
                )
    finally:
        await conn.close()


def main() -> None:
    """CLI entry point for the pooler-backed catalog seed."""
    parser = argparse.ArgumentParser(description="Seed the catalog via the IPv4 session pooler (asyncpg).")
    parser.add_argument("--type", choices=["channels", "podcasts", "x", "personalities"], default=None)
    parser.add_argument("--archetype", choices=sorted(ALLOWED_ARCHETYPES), default=None)
    parser.add_argument("--all-archetypes", action="store_true", help="Seed --type across all 12 archetypes.")
    parser.add_argument("--selftest", action="store_true", help="Validate the write path then exit.")
    args = parser.parse_args()
    if not args.selftest and not args.all_archetypes and args.archetype is None:
        parser.error("pass --archetype <slug>, --all-archetypes, or --selftest")
    asyncio.run(_run(args))


if __name__ == "__main__":
    main()
