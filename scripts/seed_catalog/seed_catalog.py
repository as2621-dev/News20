"""Seed the per-archetype curated content-source catalog from JSON files.

Ported from TL;DW (``scripts/seed_catalog/seed_catalog.py``) per
reference/sources-reuse-map.md §2 and re-targeted to News20's migration-0009
schema. Reads ``data/{type}.{archetype}.json`` (file array position = popularity
rank) and upserts each entry into Supabase via the service-role client:

  - ``channels.{archetype}.json``      → resolve via YouTube ``channels.list``
    → ``content_sources`` (``content_source_type='youtube_channel'``).
  - ``podcasts.{archetype}.json``      → resolve via iTunes search (``feed_url``
    captured into ``platform_metadata``) → ``content_sources`` (``'podcast'``).
  - ``x.{archetype}.json``             → stored as ``content_sources``
    (``'x_account'``) WITHOUT live resolution (no resolver until 5c/5d) —
    ``external_id`` is the handle, no thumbnail fetch.
  - ``personalities.{archetype}.json`` → Wikipedia photo lookup → ``personalities``.

Each row is tagged with the UNION of the archetype ``personas`` it appears under
across files (cross-archetype overlap collapses at upsert time), the 8-category
``topic_tags`` the JSON declares, and a ``popularity_score`` derived from the
file position (rank 0 → 100, descending).

Idempotent: re-running upserts on the 0009 unique keys
(``content_sources (content_source_type, external_id)`` /
``personalities (display_name)``), so no duplicate rows accrue.

Divergences from the donor (Rule 7): table ``sources`` → ``content_sources`` and
column ``source_type`` → ``content_source_type`` (News20 naming-collision guard);
the donor's 6 personas re-authored to News20's 12 SP2 archetype slugs; a new
``x_account`` type stored without a resolver; the donor's ``personality_sources``
linking pass is dropped (5d owns appearance linking).

Usage:

    export $(grep -v '^#' .env | xargs)            # SUPABASE_URL/KEY, YOUTUBE_API_KEY
    python -m scripts.seed_catalog.seed_catalog                 # full run
    python -m scripts.seed_catalog.seed_catalog --dry-run       # resolve, no writes
    python -m scripts.seed_catalog.seed_catalog --type channels --archetype ai-frontier-tech
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from agents.shared.logger import get_logger
from agents.shared.settings import Settings
from scripts.seed_catalog import itunes_resolve, youtube_resolve

logger = get_logger("seed_catalog.seed_catalog")

DATA_DIR = Path(__file__).resolve().parent / "data"

# The 12 SP2 archetype slugs (supabase/seed/archetypes.sql) — the ONLY valid
# `personas` tags. A file keyed to an unknown archetype is skipped (fail loud).
ALLOWED_ARCHETYPES: frozenset[str] = frozenset(
    {
        "ai-frontier-tech",
        "markets-macro",
        "startup-operator",
        "crypto-fintech",
        "geopolitics-world",
        "us-politics-policy",
        "climate-energy",
        "sports-fan",
        "arts-culture",
        "creator-media",
        "tech-generalist",
        "balanced-generalist",
    }
)

# The 8 pinned topic categories (axis C1) — the ONLY valid `topic_tags`.
ALLOWED_TOPIC_TAGS: frozenset[str] = frozenset(
    {
        "ai",
        "geopolitics",
        "business",
        "environment",
        "politics",
        "tech",
        "sport",
        "arts",
    }
)

# File-name `{type}` → handling. `x` → x_account (no resolver).
ALLOWED_TYPES: frozenset[str] = frozenset(
    {"channels", "podcasts", "x", "personalities"}
)

# Popularity is derived from file rank: position 0 is most popular. Linear decay
# from POPULARITY_TOP down by POPULARITY_STEP per rank, floored at POPULARITY_FLOOR.
POPULARITY_TOP = 100.0
POPULARITY_STEP = 2.0
POPULARITY_FLOOR = 10.0

WIKIPEDIA_API_BASE = "https://en.wikipedia.org/api/rest_v1"
WIKIPEDIA_USER_AGENT = "News20-SeedCatalog/1.0 (+https://news20.app)"
WIKIPEDIA_TIMEOUT_SECONDS = 10.0


# ── Models ────────────────────────────────────────────────────────────────────


class CatalogEntry(BaseModel):
    """One curated catalog entry after persona-union merge across archetype files.

    The merge collapses the same source seen under multiple archetype files into a
    single entry whose ``personas`` is the union and whose ``rank`` is the minimum
    (earliest / most-popular) position seen.

    Attributes:
        dedup_key: The cross-file identity key (lowercased handle / search term /
            display name) used to collapse duplicates.
        entry_type: One of ``channels`` / ``podcasts`` / ``x`` / ``personalities``.
        personas: Union of the archetype slugs this entry appears under.
        topic_tags: The 8-category tags declared for this entry.
        rank: Minimum file position seen (0 = most popular) → popularity_score.
        payload: The raw JSON object (handle / search_term / display_name / etc.).
    """

    dedup_key: str = Field(..., description="Cross-file identity key (lowercased).")
    entry_type: str = Field(..., description="channels | podcasts | x | personalities.")
    personas: list[str] = Field(
        default_factory=list, description="Union of archetype slugs."
    )
    topic_tags: list[str] = Field(
        default_factory=list, description="8-category topic tags."
    )
    rank: int = Field(
        default=0, ge=0, description="Min file position (0 = most popular)."
    )
    payload: dict[str, Any] = Field(default_factory=dict, description="Raw JSON entry.")

    @property
    def popularity_score(self) -> float:
        """Map the file rank to a popularity score (rank 0 → top, descending).

        Returns:
            ``max(POPULARITY_FLOOR, POPULARITY_TOP - rank * POPULARITY_STEP)``.
        """
        return max(POPULARITY_FLOOR, POPULARITY_TOP - self.rank * POPULARITY_STEP)


class SeedSummary(BaseModel):
    """Counts of what one seed run upserted, for logging + the DoD assertion.

    Attributes:
        channels_upserted: ``content_sources`` rows upserted as youtube_channel.
        podcasts_upserted: ``content_sources`` rows upserted as podcast.
        x_accounts_upserted: ``content_sources`` rows upserted as x_account.
        personalities_upserted: ``personalities`` rows upserted.
        channels_unresolved: Channel entries YouTube could not resolve (skipped).
        podcasts_unresolved: Podcast entries iTunes could not resolve (skipped).

    Example:
        >>> SeedSummary(channels_upserted=12).channels_upserted
        12
    """

    channels_upserted: int = Field(default=0, ge=0)
    podcasts_upserted: int = Field(default=0, ge=0)
    x_accounts_upserted: int = Field(default=0, ge=0)
    personalities_upserted: int = Field(default=0, ge=0)
    channels_unresolved: int = Field(default=0, ge=0)
    podcasts_unresolved: int = Field(default=0, ge=0)


# ── Supabase client (live path only) ──────────────────────────────────────────


def make_admin_client() -> Any:
    """Build a service-role Supabase client for the live seed run.

    Mirrors News20's persist idiom (``supabase.create_client`` with the
    service-role key, which bypasses RLS — only this seeder writes the catalog).
    Imported lazily so the test suite never needs ``supabase`` installed or any
    key set; tests inject a mock client instead.

    Returns:
        A ``supabase.Client`` bound to the service-role key.

    Raises:
        RuntimeError: When ``SUPABASE_URL`` / ``SUPABASE_SERVICE_ROLE_KEY`` are unset.
    """
    url = os.environ.get("SUPABASE_URL", "").strip()
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not url or not service_key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set to seed the catalog. "
            "fix_suggestion: export them from the project .env before running seed_catalog."
        )
    from supabase import create_client  # noqa: PLC0415 — lazy so tests need no supabase

    return create_client(url, service_key)


# ── JSON loading + persona-union merge ────────────────────────────────────────


def load_entries(
    *,
    type_filter: str | None = None,
    archetype_filter: str | None = None,
    data_dir: Path = DATA_DIR,
) -> dict[str, list[CatalogEntry]]:
    """Load and persona-union-merge all ``data/{type}.{archetype}.json`` files.

    The file array position is the popularity rank (index 0 = most popular). The
    same source appearing under multiple archetype files is collapsed into one
    :class:`CatalogEntry` whose ``personas`` is the union of every archetype it
    appears under and whose ``rank`` is the minimum position seen.

    Args:
        type_filter: When set, only load this ``{type}`` (channels/podcasts/x/personalities).
        archetype_filter: When set, only load this archetype's files.
        data_dir: The directory holding the JSON files (overridable for tests).

    Returns:
        ``{entry_type: [CatalogEntry, ...]}`` grouped by type, each list merged.

    Raises:
        FileNotFoundError: When ``data_dir`` does not exist.
    """
    if not data_dir.exists():
        raise FileNotFoundError(
            f"Catalog data dir not found at {data_dir}. "
            "fix_suggestion: create scripts/seed_catalog/data and add curated JSON files."
        )

    merged_by_type: dict[str, dict[str, CatalogEntry]] = {}
    for path in sorted(data_dir.glob("*.json")):
        type_part, archetype_part = _split_filename(path)
        if type_part is None or archetype_part is None:
            continue
        if type_filter and type_part != type_filter:
            continue
        if archetype_filter and archetype_part != archetype_filter:
            continue

        entries = _read_json_array(path)
        if entries is None:
            continue

        key_fn = _dedup_key_fn(type_part)
        bucket = merged_by_type.setdefault(type_part, {})
        for position, raw in enumerate(entries):
            _merge_one(bucket, raw, type_part, archetype_part, position, key_fn, path)
        logger.info(
            "seed_catalog_loaded_file", filename=path.name, entry_count=len(entries)
        )

    return {
        entry_type: list(bucket.values())
        for entry_type, bucket in merged_by_type.items()
    }


def _split_filename(path: Path) -> tuple[str | None, str | None]:
    """Split ``{type}.{archetype}.json`` into its type + archetype, validating both.

    Args:
        path: The JSON file path.

    Returns:
        ``(type, archetype)`` when valid, else ``(None, None)`` (logged + skipped).
    """
    try:
        type_part, archetype_part = path.stem.split(".", maxsplit=1)
    except ValueError:
        logger.warning(
            "seed_catalog_skipping_unparseable_filename",
            filename=path.name,
            fix_suggestion="Use the {type}.{archetype}.json convention.",
        )
        return None, None
    if type_part not in ALLOWED_TYPES:
        logger.warning(
            "seed_catalog_skipping_unknown_type",
            filename=path.name,
            type_part=type_part,
            fix_suggestion=f"Type must be one of {sorted(ALLOWED_TYPES)}.",
        )
        return None, None
    if archetype_part not in ALLOWED_ARCHETYPES:
        logger.warning(
            "seed_catalog_skipping_unknown_archetype",
            filename=path.name,
            archetype_part=archetype_part,
            fix_suggestion=f"Archetype must be one of the 12 SP2 slugs: {sorted(ALLOWED_ARCHETYPES)}.",
        )
        return None, None
    return type_part, archetype_part


def _read_json_array(path: Path) -> list[dict[str, Any]] | None:
    """Read a JSON file expected to hold an array of objects.

    Args:
        path: The JSON file path.

    Returns:
        The parsed list, or None on a parse error / non-array body (logged + skipped).
    """
    try:
        parsed = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        logger.error(
            "seed_catalog_invalid_json",
            filename=path.name,
            error_message=str(exc),
            fix_suggestion="Run the file through `jq .` to locate the parse error.",
        )
        return None
    if not isinstance(parsed, list):
        logger.error(
            "seed_catalog_not_an_array",
            filename=path.name,
            fix_suggestion="Each data file must be a JSON array of source objects.",
        )
        return None
    return parsed


def _dedup_key_fn(type_part: str) -> Callable[[dict[str, Any]], str | None]:
    """Return the cross-file dedup-key extractor for a given entry type.

    Args:
        type_part: The entry type (channels/podcasts/x/personalities).

    Returns:
        A function mapping a raw JSON entry to its lowercased identity key (or None).
    """
    if type_part == "channels":
        return lambda raw: (
            (raw.get("youtube_handle") or raw.get("channel_id") or "").lower() or None
        )
    if type_part == "podcasts":
        return lambda raw: (
            (raw.get("search_term") or raw.get("source_name") or "").strip().lower()
            or None
        )
    if type_part == "x":
        return lambda raw: (raw.get("handle") or "").lstrip("@").strip().lower() or None
    return lambda raw: (raw.get("display_name") or "").strip().lower() or None


def _merge_one(
    bucket: dict[str, CatalogEntry],
    raw: dict[str, Any],
    type_part: str,
    archetype_part: str,
    position: int,
    key_fn: Callable[[dict[str, Any]], str | None],
    path: Path,
) -> None:
    """Merge one raw JSON entry into the per-type bucket (persona-union, min-rank).

    Args:
        bucket: The ``{dedup_key: CatalogEntry}`` accumulator for this type.
        raw: The raw JSON object.
        type_part: The entry type.
        archetype_part: The archetype slug from the filename.
        position: The entry's index in its file (the popularity rank).
        key_fn: The dedup-key extractor for this type.
        path: The source file (for log context only).
    """
    key = key_fn(raw)
    if not key:
        logger.warning(
            "seed_catalog_entry_missing_identity",
            filename=path.name,
            position=position,
            fix_suggestion="Each entry needs a handle / search_term / display_name identity field.",
        )
        return
    # The archetype slug from the FILENAME is the authoritative persona; any
    # `personas` declared inline (donor compatibility) is unioned in too.
    entry_personas = {archetype_part, *(raw.get("personas") or [])}
    entry_tags = list(raw.get("topic_tags") or [])
    existing = bucket.get(key)
    if existing is None:
        bucket[key] = CatalogEntry(
            dedup_key=key,
            entry_type=type_part,
            personas=sorted(entry_personas),
            topic_tags=sorted(set(entry_tags)),
            rank=position,
            payload=raw,
        )
        return
    existing.personas = sorted({*existing.personas, *entry_personas})
    existing.topic_tags = sorted({*existing.topic_tags, *entry_tags})
    existing.rank = min(existing.rank, position)


# ── Wikipedia photo lookup (personalities) ────────────────────────────────────


async def fetch_wikipedia_photo(
    *, slug: str | None, display_name: str, client: httpx.AsyncClient
) -> str | None:
    """Resolve a Wikipedia lead-image URL for a personality.

    Hits the REST ``page/summary/{slug}`` endpoint and returns the highest-res
    ``originalimage.source`` (else ``thumbnail.source``). The curator-supplied
    ``slug`` is tried first, falling back to the URL-safe display name. Returns
    None on any miss / error — the caller keeps any photo already on the row,
    else the app's initials-on-gradient avatar fallback covers it (never raises).

    Args:
        slug: Curator-supplied article slug (e.g. ``Andrej_Karpathy``). Optional.
        display_name: Personality display name (used as the slug fallback).
        client: An injected ``httpx.AsyncClient``.

    Returns:
        An https image URL, or None when no usable image exists.
    """
    candidates = [
        slug.strip() for slug in (slug, display_name) if slug and slug.strip()
    ]
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.replace(" ", "_")
        if normalized in seen:
            continue
        seen.add(normalized)
        image_url = await _wikipedia_summary_image(normalized, client=client)
        if image_url:
            return image_url
    return None


async def _wikipedia_summary_image(
    slug: str, *, client: httpx.AsyncClient
) -> str | None:
    """Fetch ``page/summary/{slug}`` and return its best image URL or None.

    Args:
        slug: The (underscored) article slug.
        client: An injected ``httpx.AsyncClient``.

    Returns:
        The original or thumbnail image URL, or None on 404 / non-200 / error.
    """
    import urllib.parse  # noqa: PLC0415 — only needed on the personalities path

    encoded = urllib.parse.quote(slug, safe="")
    url = f"{WIKIPEDIA_API_BASE}/page/summary/{encoded}"
    try:
        response = await client.get(url, timeout=WIKIPEDIA_TIMEOUT_SECONDS)
    except httpx.HTTPError as exc:
        logger.warning(
            "wikipedia_photo_http_error",
            slug=slug,
            error_message=str(exc),
            fix_suggestion="Inspect Wikipedia REST connectivity; the row keeps its avatar fallback.",
        )
        return None
    if response.status_code != 200:
        return None
    payload: dict[str, Any] = response.json()
    original = (payload.get("originalimage") or {}).get("source")
    thumbnail = (payload.get("thumbnail") or {}).get("source")
    return original or thumbnail


# ── Row builders (typed upsert payloads) ──────────────────────────────────────


def build_channel_row(
    entry: CatalogEntry, meta: youtube_resolve.ChannelMeta
) -> dict[str, Any]:
    """Build a ``content_sources`` upsert row for a resolved YouTube channel.

    Args:
        entry: The merged catalog entry (personas / topic_tags / popularity).
        meta: The resolved channel metadata.

    Returns:
        The column payload for an upsert on ``(content_source_type, external_id)``.
    """
    return {
        "content_source_type": "youtube_channel",
        "external_id": meta.channel_id,
        "source_name": meta.title
        or entry.payload.get("source_name")
        or entry.dedup_key,
        "source_description": meta.description,
        "thumbnail_url": meta.thumbnail_url,
        "subscriber_count": meta.subscriber_count,
        "personas": entry.personas,
        "topic_tags": entry.topic_tags,
        "popularity_score": entry.popularity_score,
        "is_curated": True,
    }


def build_podcast_row(
    entry: CatalogEntry, meta: itunes_resolve.PodcastMeta
) -> dict[str, Any]:
    """Build a ``content_sources`` upsert row for a resolved podcast.

    Captures the RSS ``feed_url`` into ``platform_metadata`` (the 5d ingestion
    reads it). ``subscriber_count`` is None (iTunes has no follower count).

    Args:
        entry: The merged catalog entry.
        meta: The resolved podcast metadata.

    Returns:
        The column payload for an upsert on ``(content_source_type, external_id)``.
    """
    description = (
        f"{meta.track_count} episodes · {meta.artist_name}"
        if meta.track_count and meta.artist_name
        else meta.artist_name
    )
    return {
        "content_source_type": "podcast",
        "external_id": meta.external_id,
        "source_name": meta.name,
        "source_description": description,
        "thumbnail_url": meta.artwork_url,
        "subscriber_count": None,
        "platform_metadata": {"feed_url": meta.feed_url} if meta.feed_url else None,
        "personas": entry.personas,
        "topic_tags": entry.topic_tags,
        "popularity_score": entry.popularity_score,
        "is_curated": True,
    }


def build_x_account_row(entry: CatalogEntry) -> dict[str, Any]:
    """Build a ``content_sources`` upsert row for an X handle (NO live resolution).

    The handle is the ``external_id`` verbatim; no thumbnail / follower fetch
    happens here — the X resolver is built in Phase 5c/5d. ``last_fetched_at``
    stays null so 5d knows the row is unresolved.

    Args:
        entry: The merged catalog entry (``payload['handle']`` is the X handle).

    Returns:
        The column payload for an upsert on ``(content_source_type, external_id)``.
    """
    handle = (entry.payload.get("handle") or "").lstrip("@").strip()
    return {
        "content_source_type": "x_account",
        "external_id": handle,
        "source_name": entry.payload.get("source_name") or f"@{handle}",
        "source_description": entry.payload.get("bio"),
        "thumbnail_url": None,
        "subscriber_count": None,
        "personas": entry.personas,
        "topic_tags": entry.topic_tags,
        "popularity_score": entry.popularity_score,
        "is_curated": True,
    }


def build_personality_row(entry: CatalogEntry, photo_url: str | None) -> dict[str, Any]:
    """Build a ``personalities`` upsert row.

    Args:
        entry: The merged catalog entry (``payload`` carries display_name/bio/aliases).
        photo_url: The resolved Wikipedia photo URL (None → app avatar fallback).

    Returns:
        The column payload for an upsert on ``display_name``.
    """
    payload = entry.payload
    return {
        "display_name": payload["display_name"],
        "aliases": payload.get("aliases") or [],
        "bio": payload.get("bio"),
        "photo_url": photo_url or payload.get("photo_url"),
        "youtube_channel_ids": payload.get("youtube_channel_ids") or [],
        "personas": entry.personas,
        "topic_tags": entry.topic_tags,
        "popularity_score": entry.popularity_score,
        "is_curated": True,
    }


# ── Upsert helpers (boundary to Supabase) ─────────────────────────────────────


def _upsert_content_sources(
    supabase_client: Any, rows: list[dict[str, Any]], *, dry_run: bool
) -> int:
    """Upsert a batch of ``content_sources`` rows on the 0009 unique key.

    Args:
        supabase_client: The injected (real or mock) Supabase client. None on dry-run.
        rows: The column payloads to upsert.
        dry_run: When True, log each row and write nothing.

    Returns:
        The number of rows submitted for upsert (== ``len(rows)``).
    """
    if not rows:
        return 0
    if dry_run or supabase_client is None:
        for row in rows:
            logger.info(
                "seed_catalog_content_source_dry_run", external_id=row["external_id"]
            )
        return len(rows)
    supabase_client.table("content_sources").upsert(
        rows, on_conflict="content_source_type,external_id"
    ).execute()
    return len(rows)


def _upsert_personalities(
    supabase_client: Any, rows: list[dict[str, Any]], *, dry_run: bool
) -> int:
    """Upsert a batch of ``personalities`` rows on the ``display_name`` unique key.

    Args:
        supabase_client: The injected (real or mock) Supabase client. None on dry-run.
        rows: The column payloads to upsert.
        dry_run: When True, log each row and write nothing.

    Returns:
        The number of rows submitted for upsert (== ``len(rows)``).
    """
    if not rows:
        return 0
    if dry_run or supabase_client is None:
        for row in rows:
            logger.info(
                "seed_catalog_personality_dry_run", display_name=row["display_name"]
            )
        return len(rows)
    supabase_client.table("personalities").upsert(
        rows, on_conflict="display_name"
    ).execute()
    return len(rows)


# ── Per-type seeding ──────────────────────────────────────────────────────────


async def seed_channels(
    entries: list[CatalogEntry],
    *,
    supabase_client: Any,
    http_client: httpx.AsyncClient,
    api_key: str,
    dry_run: bool,
    summary: SeedSummary,
) -> None:
    """Resolve channel entries via YouTube and upsert the resolved rows.

    Unresolved handles are logged + skipped (counted on the summary), never
    fatal — one bad handle cannot abort the batch (Rule 12: fail loud per row,
    not the whole run).
    """
    if not entries:
        return
    metas = await youtube_resolve.resolve_many(
        [entry.payload for entry in entries], api_key=api_key, client=http_client
    )
    rows: list[dict[str, Any]] = []
    for entry in entries:
        meta = metas.get(entry.dedup_key)
        if meta is None:
            summary.channels_unresolved += 1
            logger.warning(
                "seed_channel_unresolved",
                dedup_key=entry.dedup_key,
                fix_suggestion="Confirm the YouTube handle is correct and the channel exists.",
            )
            continue
        rows.append(build_channel_row(entry, meta))
    summary.channels_upserted = _upsert_content_sources(
        supabase_client, rows, dry_run=dry_run
    )


async def seed_podcasts(
    entries: list[CatalogEntry],
    *,
    supabase_client: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
    summary: SeedSummary,
) -> None:
    """Resolve podcast entries via iTunes and upsert the resolved rows."""
    if not entries:
        return
    names = [_podcast_search_term(entry) for entry in entries]
    metas = await itunes_resolve.resolve_many(
        [name for name in names if name], client=http_client
    )
    rows: list[dict[str, Any]] = []
    for entry in entries:
        meta = metas.get(_podcast_search_term(entry))
        if meta is None:
            summary.podcasts_unresolved += 1
            logger.warning(
                "seed_podcast_unresolved",
                dedup_key=entry.dedup_key,
                fix_suggestion="Try a different search term — iTunes may not have this title.",
            )
            continue
        rows.append(build_podcast_row(entry, meta))
    summary.podcasts_upserted = _upsert_content_sources(
        supabase_client, rows, dry_run=dry_run
    )


def _podcast_search_term(entry: CatalogEntry) -> str:
    """Return the iTunes search term for a podcast entry.

    Args:
        entry: The merged catalog entry.

    Returns:
        The ``search_term`` (or ``source_name``) the resolver keys on.
    """
    return entry.payload.get("search_term") or entry.payload.get("source_name") or ""


def seed_x_accounts(
    entries: list[CatalogEntry],
    *,
    supabase_client: Any,
    dry_run: bool,
    summary: SeedSummary,
) -> None:
    """Upsert X-account entries WITHOUT live resolution (handle = external_id)."""
    if not entries:
        return
    rows = [
        build_x_account_row(entry) for entry in entries if entry.payload.get("handle")
    ]
    summary.x_accounts_upserted = _upsert_content_sources(
        supabase_client, rows, dry_run=dry_run
    )


async def seed_personalities(
    entries: list[CatalogEntry],
    *,
    supabase_client: Any,
    http_client: httpx.AsyncClient,
    dry_run: bool,
    summary: SeedSummary,
) -> None:
    """Resolve personality photos via Wikipedia and upsert the rows."""
    if not entries:
        return
    photos = await asyncio.gather(
        *(
            fetch_wikipedia_photo(
                slug=entry.payload.get("wikipedia_slug"),
                display_name=entry.payload["display_name"],
                client=http_client,
            )
            for entry in entries
        )
    )
    rows = [
        build_personality_row(entry, photo) for entry, photo in zip(entries, photos)
    ]
    summary.personalities_upserted = _upsert_personalities(
        supabase_client, rows, dry_run=dry_run
    )


# ── Orchestration ─────────────────────────────────────────────────────────────


async def run_seed(
    *,
    supabase_client: Any,
    http_client: httpx.AsyncClient,
    youtube_api_key: str,
    type_filter: str | None = None,
    archetype_filter: str | None = None,
    dry_run: bool = False,
    data_dir: Path = DATA_DIR,
) -> SeedSummary:
    """Run the full catalog seed end-to-end against an INJECTED client pair.

    Both the Supabase client and the httpx client are injected so the test suite
    mocks both at the boundary (CLAUDE.md) and the live entry point wires the real
    ones. Resolves + upserts channels → podcasts → x → personalities.

    Args:
        supabase_client: A service-role Supabase client (None on dry-run).
        http_client: An ``httpx.AsyncClient`` for all resolver calls.
        youtube_api_key: The YouTube Data API v3 key.
        type_filter: Optional single ``{type}`` to seed.
        archetype_filter: Optional single archetype to seed.
        dry_run: When True, resolve but write nothing.
        data_dir: The JSON data directory (overridable for tests).

    Returns:
        A :class:`SeedSummary` of upsert + unresolved counts.
    """
    entries_by_type = load_entries(
        type_filter=type_filter, archetype_filter=archetype_filter, data_dir=data_dir
    )
    summary = SeedSummary()

    if entries_by_type.get("channels"):
        await seed_channels(
            entries_by_type["channels"],
            supabase_client=supabase_client,
            http_client=http_client,
            api_key=youtube_api_key,
            dry_run=dry_run,
            summary=summary,
        )
    if entries_by_type.get("podcasts"):
        await seed_podcasts(
            entries_by_type["podcasts"],
            supabase_client=supabase_client,
            http_client=http_client,
            dry_run=dry_run,
            summary=summary,
        )
    if entries_by_type.get("x"):
        seed_x_accounts(
            entries_by_type["x"],
            supabase_client=supabase_client,
            dry_run=dry_run,
            summary=summary,
        )
    if entries_by_type.get("personalities"):
        await seed_personalities(
            entries_by_type["personalities"],
            supabase_client=supabase_client,
            http_client=http_client,
            dry_run=dry_run,
            summary=summary,
        )

    logger.info(
        "seed_catalog_completed",
        channels_upserted=summary.channels_upserted,
        podcasts_upserted=summary.podcasts_upserted,
        x_accounts_upserted=summary.x_accounts_upserted,
        personalities_upserted=summary.personalities_upserted,
        channels_unresolved=summary.channels_unresolved,
        podcasts_unresolved=summary.podcasts_unresolved,
    )
    return summary


async def _main_async(args: argparse.Namespace) -> None:
    """Wire the live clients/keys and run the seed (CLI path only).

    Args:
        args: Parsed CLI arguments (``--type`` / ``--archetype`` / ``--dry-run``).

    Raises:
        RuntimeError: When ``YOUTUBE_API_KEY`` is missing on a non-dry channel seed.
    """
    settings = Settings()
    youtube_api_key = (settings.youtube_api_key or "").strip()
    needs_youtube = args.type in (None, "channels")
    if needs_youtube and not args.dry_run and not youtube_api_key:
        raise RuntimeError(
            "YOUTUBE_API_KEY is required to resolve channels. "
            "fix_suggestion: export YOUTUBE_API_KEY, or pass --dry-run / --type podcasts."
        )

    supabase_client = None if args.dry_run else make_admin_client()
    async with httpx.AsyncClient(
        headers={"User-Agent": WIKIPEDIA_USER_AGENT}
    ) as http_client:
        await run_seed(
            supabase_client=supabase_client,
            http_client=http_client,
            youtube_api_key=youtube_api_key,
            type_filter=args.type,
            archetype_filter=args.archetype,
            dry_run=args.dry_run,
        )


def main() -> None:
    """CLI entry point for the catalog seeder."""
    parser = argparse.ArgumentParser(
        description="Seed the per-archetype content-source catalog."
    )
    parser.add_argument(
        "--type", choices=sorted(ALLOWED_TYPES), help="Only seed this type."
    )
    parser.add_argument(
        "--archetype",
        choices=sorted(ALLOWED_ARCHETYPES),
        help="Only seed this archetype's files.",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Resolve but do not write to Supabase."
    )
    asyncio.run(_main_async(parser.parse_args()))


if __name__ == "__main__":
    main()
