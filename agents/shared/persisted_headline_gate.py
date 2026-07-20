"""Applying the headline gate to a row that is ALREADY in ``stories``.

The write-time gate (``agents/pipeline/persist_helpers.reject_unpublishable_headline``)
runs on an in-flight :class:`~agents.ingestion.models.CanonicalStory`, which still knows
its outlet domain. A read path has only the persisted row, and rows written before that
gate existed can still carry a masthead title, so two seams re-check them:

  * ``agents/worker/pipeline_routes.py::_load_ready_story_pool`` — the one production
    path that puts a persisted row back into a feed.
  * ``scripts/retire_unpublishable_headlines.py`` — the one-time cleanup that retires
    the digests of rows that fail.

Those two MUST reach identical verdicts: a story the script retires but the loader would
place is a lost reel, and one the loader drops but the script leaves is a permanent
silent skip. So the row-shaped verdict and the source-origin lookup both live here, once.
"""

from __future__ import annotations

from typing import Any

from agents.ingestion.dedup import source_origin_story_ids_from_source_rows
from agents.shared.headline_quality import headline_rejection_reason

# Reason: same chunking the daily batch uses for ``.in_()`` — a large id list overflows
# the request URL (agents/pipeline/daily_batch.py::_load_has_current_digest).
_ID_CHUNK_SIZE = 150
# Reason: PostgREST caps a response (1000 by default). ``story_sources`` holds one row
# per covering outlet, so a few hundred stories blow past that — page every chunk or the
# source-origin exemption silently applies to an arbitrary subset.
_PAGE_SIZE = 1000


def persisted_headline_rejection_reason(
    story_row: dict[str, Any],
    source_origin_ids: set[str],
) -> str | None:
    """Why this persisted story cannot be placed, or ``None`` if it can.

    Args:
        story_row: A ``stories`` row with ``story_id``, ``story_headline`` and
            ``story_primary_outlet_name``.
        source_origin_ids: Story ids that are followed-source (YouTube/X) reels, from
            :func:`load_source_origin_story_ids`.

    Returns:
        A short machine-readable reason (``"empty"``, ``"title_equals_outlet"``,
        ``"too_few_words"``), or ``None`` when the story may be placed.

    Example:
        >>> persisted_headline_rejection_reason(
        ...     {"story_id": "s1", "story_headline": "Language Magazine",
        ...      "story_primary_outlet_name": "languagemagazine.com"}, set()
        ... )
        'title_equals_outlet'
    """
    # Reason: followed-source reels carry creator-written upload titles, not scraped
    # <PAGE_TITLE>s, so they are exempt exactly as at the produce gate and the write-time
    # gate. Dropping one would remove a reel the user explicitly subscribed to.
    if str(story_row["story_id"]) in source_origin_ids:
        return None
    outlet_name = story_row.get("story_primary_outlet_name")
    # Reason: the column holds a DISPLAY name for some rows and a bare domain for others
    # (prod carries "languagemagazine.com" for the 07-07 masthead story), so it is
    # offered as both and the predicate unions the two key sets. A value with no dot
    # yields no domain keys, so this never tightens the gate on a genuine headline.
    outlet_hint = str(outlet_name) if outlet_name else None
    return headline_rejection_reason(
        str(story_row.get("story_headline") or ""), outlet_hint, outlet_hint
    )


def load_source_origin_story_ids(
    supabase_client: Any, story_ids: list[str]
) -> set[str]:
    """Of ``story_ids``, which are followed-source (YouTube/X) reels.

    Reads the citation URLs on ``story_sources``, because ``stories`` does not record the
    origin — ``story_primary_outlet_name`` is the channel/creator name for a source reel,
    never ``youtube.com``/``x.com``.

    The ids are chunked and every chunk is paged: ``story_sources`` holds one row per
    covering outlet and only the primary row carries a URL, so an unpaged read would
    truncate away exactly the rows the exemption depends on.

    Args:
        supabase_client: Service-role Supabase client.
        story_ids: The story ids to classify.

    Returns:
        The subset of ``story_ids`` that are followed-source reels.
    """
    source_rows: list[dict[str, Any]] = []
    for start in range(0, len(story_ids), _ID_CHUNK_SIZE):
        chunk = story_ids[start : start + _ID_CHUNK_SIZE]
        offset = 0
        while True:
            page = (
                getattr(
                    supabase_client.table("story_sources")
                    .select("source_story_id,source_article_url")
                    .in_("source_story_id", chunk)
                    .order("story_source_id")
                    .range(offset, offset + _PAGE_SIZE - 1)
                    .execute(),
                    "data",
                    None,
                )
                or []
            )
            source_rows.extend(page)
            # Reason: stop on an EMPTY page, not a short one — the project's max-rows may
            # be below _PAGE_SIZE, and a short-page exit would then read only page one.
            if not page:
                break
            offset += len(page)
    return source_origin_story_ids_from_source_rows(source_rows)
